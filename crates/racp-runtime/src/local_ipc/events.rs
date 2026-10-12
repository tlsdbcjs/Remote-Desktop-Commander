//! Snapshot and receiver registration share one lock; lag forces an explicit resnapshot.
use racp_contract::{local_ipc::*, RacpError};
use std::sync::Arc;
use tokio::sync::{broadcast, watch, Mutex};

#[derive(Clone)]
pub struct StateHub {
    inner: Arc<Mutex<Inner>>,
    presence: watch::Sender<usize>,
}
struct Inner {
    snapshot: StateSnapshot,
    sender: broadcast::Sender<StateEvent>,
}
pub struct Subscription {
    pub snapshot: StateSnapshot,
    receiver: broadcast::Receiver<StateEvent>,
    presence: watch::Sender<usize>,
    controller: bool,
}
impl StateHub {
    pub fn new(snapshot: StateSnapshot) -> Result<Self, RacpError> {
        encode(&snapshot)?;
        let (sender, _) = broadcast::channel(EVENT_QUEUE_CAPACITY);
        let (presence, _) = watch::channel(0);
        Ok(Self {
            inner: Arc::new(Mutex::new(Inner { snapshot, sender })),
            presence,
        })
    }
    pub async fn snapshot(&self) -> StateSnapshot {
        self.inner.lock().await.snapshot.clone()
    }
    pub async fn subscribe(&self) -> Subscription {
        self.subscribe_controller(true).await
    }
    pub async fn subscribe_controller(&self, controller: bool) -> Subscription {
        let inner = self.inner.lock().await;
        if controller {
            self.presence.send_modify(|count| *count += 1);
        }
        Subscription {
            snapshot: inner.snapshot.clone(),
            receiver: inner.sender.subscribe(),
            presence: self.presence.clone(),
            controller,
        }
    }
    pub fn presence(&self) -> watch::Receiver<usize> {
        self.presence.subscribe()
    }
    pub async fn publish(&self, revision: u64, change: StateChange) -> Result<(), RacpError> {
        let mut inner = self.inner.lock().await;
        Self::publish_locked(&mut inner, revision, change)
    }
    pub async fn activity(&self, item: ActivitySummary) -> Result<(), RacpError> {
        let mut inner = self.inner.lock().await;
        let rank = |state: &str| match state {
            "ACCEPTED" => 0,
            "DISPATCHED" => 1,
            "RUNNING" | "RECONCILING" => 2,
            "CANCEL_REQUESTED" => 3,
            _ => 4,
        };
        let mut activity = inner.snapshot.activity.clone();
        if let Some(old) = activity
            .iter_mut()
            .find(|old| old.operation_id == item.operation_id)
        {
            // A delayed publisher cannot roll terminal/cancel metadata backwards.
            if old.state == item.state || rank(&old.state) > rank(&item.state) {
                return Ok(());
            }
            *old = item;
        } else {
            activity.push(item);
        }
        while activity.len() > 32 {
            let index = activity
                .iter()
                .position(|item| rank(&item.state) == 4)
                .unwrap_or(0);
            activity.remove(index);
        }
        let revision = inner.snapshot.policy_revision;
        Self::publish_locked(&mut inner, revision, StateChange::Activity { activity })
    }
    fn publish_locked(
        inner: &mut Inner,
        revision: u64,
        change: StateChange,
    ) -> Result<(), RacpError> {
        if revision < inner.snapshot.policy_revision {
            return Err(RacpError::new("REVISION_CONFLICT"));
        }
        let sequence = inner
            .snapshot
            .sequence
            .checked_add(1)
            .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
        let event = StateEvent {
            boot_id: inner.snapshot.boot_id.clone(),
            sequence,
            policy_revision: revision,
            change,
        };
        encode(&event)?;
        let mut snapshot = inner.snapshot.clone();
        match &event.change {
            StateChange::Status { status } => snapshot.status = *status,
            StateChange::Activity { activity } => snapshot.activity = activity.clone(),
            StateChange::Availability { availability } => {
                snapshot.availability = availability.clone()
            }
            StateChange::PolicyCommitted { policy } => snapshot.policy = Some(policy.clone()),
            StateChange::ApprovalPending {
                nonce,
                request_id,
                operation,
                target_digest,
                expires_at,
                target_summary,
            } => {
                if snapshot.pending_approvals.len() >= 16 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
                snapshot.pending_approvals.push(ApprovalPrompt {
                    nonce: nonce.clone(),
                    request_id: request_id.clone(),
                    operation: operation.clone(),
                    target_digest: target_digest.clone(),
                    expires_at: expires_at.clone(),
                    target_summary: target_summary.clone(),
                });
            }
            StateChange::ApprovalFinished { nonce, .. } => {
                snapshot.pending_approvals.retain(|p| &p.nonce != nonce)
            }
            _ => (),
        }
        snapshot.sequence = sequence;
        snapshot.policy_revision = revision;
        if let Some(result) = ControlCompletion::from_change(&event.change) {
            snapshot.recent_control_results.push(result);
            while snapshot.recent_control_results.len() > MAX_RECENT_CONTROL_RESULTS {
                snapshot.recent_control_results.remove(0);
            }
        }
        // Keep the current authority/approval state within the frame bound.
        // Historical receipts are evicted first; a missing receipt stays unknown.
        while encode(&snapshot).is_err() && !snapshot.recent_control_results.is_empty() {
            snapshot.recent_control_results.remove(0);
        }
        encode(&snapshot)?;
        inner.snapshot = snapshot;
        let _ = inner.sender.send(event);
        Ok(())
    }
}
impl Subscription {
    pub async fn next(&mut self) -> Result<StateEvent, RacpError> {
        self.receiver.recv().await.map_err(|error| match error {
            broadcast::error::RecvError::Lagged(_) => RacpError::new("RESYNC_REQUIRED"),
            broadcast::error::RecvError::Closed => RacpError::new("DEVICE_OFFLINE"),
        })
    }
}
impl Drop for Subscription {
    fn drop(&mut self) {
        if self.controller {
            self.presence
                .send_modify(|count| *count = count.saturating_sub(1));
        }
    }
}
