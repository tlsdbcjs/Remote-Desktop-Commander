use super::events::StateHub;
use racp_contract::{local_ipc::StateChange, RacpError};
use racp_core::approval::ApprovalBinding;
use serde_json::Value;
use std::{
    collections::{BTreeMap, VecDeque},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{sync::oneshot, time::Instant};
use tokio_util::sync::CancellationToken;

struct Pending {
    binding: ApprovalBinding,
    deadline: Instant,
    decide: oneshot::Sender<bool>,
}
#[derive(Default)]
struct Ledger {
    pending: BTreeMap<String, Pending>,
    active: BTreeMap<String, ApprovalBinding>,
    completed: VecDeque<ApprovalBinding>,
}
#[derive(Clone, Default)]
pub struct Approvals {
    ledger: Arc<Mutex<Ledger>>,
}
impl Approvals {
    pub async fn wait(
        &self,
        hub: &StateHub,
        request: &Value,
        target: String,
        revision: u64,
        deadline: Instant,
        cancel: CancellationToken,
    ) -> Result<ApprovalBinding, RacpError> {
        let mut presence = hub.presence();
        if *presence.borrow() == 0 {
            return Err(RacpError::new("APPROVAL_UNAVAILABLE"));
        }
        let deadline = deadline.min(Instant::now() + Duration::from_secs(60));
        if deadline <= Instant::now() {
            return Err(RacpError::new("TIMEOUT"));
        }
        let bytes: [u8; 32] = rand::random();
        let nonce: String = bytes.iter().map(|v| format!("{v:02x}")).collect();
        let ttl = deadline
            .saturating_duration_since(Instant::now())
            .as_millis()
            .min(60_000) as i64;
        let expires = (chrono::Utc::now() + chrono::Duration::milliseconds(ttl)).to_rfc3339();
        let binding =
            ApprovalBinding::from_request(request, target, revision, nonce.clone(), expires)?;
        let (sender, mut decision) = oneshot::channel();
        {
            let mut ledger = self
                .ledger
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            if ledger.pending.len() >= 16 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            ledger.pending.insert(
                nonce.clone(),
                Pending {
                    binding: binding.clone(),
                    deadline,
                    decide: sender,
                },
            );
        }
        // Drop always consumes an outstanding nonce, including cancellation of the waiting future.
        struct Guard {
            ledger: Arc<Mutex<Ledger>>,
            nonce: String,
            hub: StateHub,
        }
        impl Drop for Guard {
            fn drop(&mut self) {
                if let Ok(mut ledger) = self.ledger.lock() {
                    if ledger.pending.remove(&self.nonce).is_some() {
                        let hub = self.hub.clone();
                        let nonce = self.nonce.clone();
                        tokio::spawn(async move {
                            let revision = hub.snapshot().await.policy_revision;
                            let _ = hub
                                .publish(
                                    revision,
                                    StateChange::ApprovalFinished {
                                        nonce,
                                        state: "cancelled".into(),
                                    },
                                )
                                .await;
                        });
                    }
                }
            }
        }
        let _guard = Guard {
            ledger: self.ledger.clone(),
            nonce: nonce.clone(),
            hub: hub.clone(),
        };
        hub.publish(
            revision,
            StateChange::ApprovalPending {
                nonce: nonce.clone(),
                request_id: binding.request_id.clone(),
                operation: binding.operation.clone(),
                target_digest: binding.target_digest.clone(),
                expires_at: binding.expires_at.clone(),
                target_summary: summary(request)?,
            },
        )
        .await?;
        let allowed = loop {
            tokio::select! {
                biased;
                _ = cancel.cancelled() => break false,
                _ = tokio::time::sleep_until(deadline) => break false,
                changed = presence.changed() => { if changed.is_err() || *presence.borrow() == 0 { break false; } },
                value = &mut decision => break value.unwrap_or(false),
            }
        };
        let current_revision = hub.snapshot().await.policy_revision;
        let _ = hub
            .publish(
                current_revision,
                StateChange::ApprovalFinished {
                    nonce,
                    state: if allowed {
                        "approved"
                    } else {
                        "denied_or_expired"
                    }
                    .into(),
                },
            )
            .await;
        if !allowed
            || current_revision != revision
            || cancel.is_cancelled()
            || Instant::now() >= deadline
        {
            return Err(RacpError::new("APPROVAL_REJECTED"));
        }
        Ok(binding)
    }
    pub fn decide(&self, nonce: &str, allow: bool, revision: u64) -> Result<(), RacpError> {
        let mut ledger = self
            .ledger
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let pending = ledger
            .pending
            .remove(nonce)
            .ok_or_else(|| RacpError::new("APPROVAL_REJECTED"))?;
        if pending.binding.policy_revision != revision || Instant::now() >= pending.deadline {
            return Err(RacpError::new("APPROVAL_REJECTED"));
        }
        pending
            .decide
            .send(allow)
            .map_err(|_| RacpError::new("APPROVAL_REJECTED"))
    }
    pub(crate) fn grant(&self, binding: ApprovalBinding) -> Result<(), RacpError> {
        let mut ledger = self
            .ledger
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        if ledger.active.len() >= 16 || ledger.active.contains_key(&binding.operation_id) {
            return Err(RacpError::new("APPROVAL_REJECTED"));
        }
        ledger.active.insert(binding.operation_id.clone(), binding);
        Ok(())
    }
    pub fn authorized(&self, request: &Value, revision: u64) -> bool {
        self.ledger.lock().is_ok_and(|ledger| {
            ledger
                .active
                .values()
                .chain(ledger.completed.iter())
                .any(|binding| binding.matches(request, revision))
        })
    }
    pub fn complete(&self, id: &str) {
        if let Ok(mut ledger) = self.ledger.lock() {
            if let Some(binding) = ledger.active.remove(id) {
                ledger.completed.push_back(binding);
                while ledger.completed.len() > 256 {
                    ledger.completed.pop_front();
                }
            }
        }
    }
    pub fn invalidate(&self) {
        if let Ok(mut ledger) = self.ledger.lock() {
            ledger.pending.clear();
            ledger.active.clear();
            ledger.completed.clear();
        }
    }
    pub fn pending(&self) -> Result<Vec<ApprovalBinding>, RacpError> {
        self.ledger
            .lock()
            .map(|ledger| ledger.pending.values().map(|p| p.binding.clone()).collect())
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))
    }
}
fn summary(request: &Value) -> Result<String, RacpError> {
    let mut summary = format!(
        "Device: {} · Owner: {} · Workspace: {}",
        request["device_id"].as_str().unwrap_or(""),
        request["context"]["principal_id"].as_str().unwrap_or(""),
        request["context"]["workspace_id"]
            .as_str()
            .unwrap_or("default")
    );
    for field in [
        "path",
        "source_path",
        "destination_path",
        "source",
        "destination",
        "expected_origin",
        "browser_id",
        "page_id",
        "frame_id",
        "navigation_revision",
        "channel",
        "pid",
        "handle_id",
        "window_id",
        "session_id",
    ] {
        if let Some(value) = request["payload"].get(field).filter(|v| !v.is_null()) {
            summary.push_str(&format!(" · {field}: {value}"));
        }
    }
    if let Some(program) = request["payload"]["argv"]
        .as_array()
        .and_then(|v| v.first())
        .and_then(Value::as_str)
    {
        summary.push_str(&format!(" · program: {program}"));
    }
    if summary.len() > 16_384 {
        return Err(RacpError::new("APPROVAL_UNAVAILABLE"));
    }
    Ok(summary)
}
