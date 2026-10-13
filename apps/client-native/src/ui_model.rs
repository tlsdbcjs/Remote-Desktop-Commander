//! Client presentation state. The Agent remains the policy authority.
use racp_contract::{local_ipc::*, RacpError};
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Deserialize)]
pub struct CatalogItem {
    pub id: String,
    pub category: String,
    pub category_label: String,
    pub label: String,
    pub implementation: String,
}
#[derive(Clone, Debug)]
pub struct UiModel {
    pub catalog: Vec<CatalogItem>,
    pub grants: BTreeMap<String, Grant>,
    pub disabled_categories: BTreeSet<String>,
    pub constraints: Option<PolicyConstraints>,
    pub snapshot: Option<StateSnapshot>,
    pub needs_resync: bool,
}
impl UiModel {
    pub fn new() -> Result<Self, RacpError> {
        let catalog: Vec<CatalogItem> = serde_json::from_str(include_str!(
            "../../../packages/protocol/src/racp_protocol/permission_catalog.json"
        ))?;
        let grants = catalog
            .iter()
            .map(|item| (item.id.clone(), Grant::Deny))
            .collect();
        Ok(Self {
            catalog,
            grants,
            disabled_categories: BTreeSet::new(),
            constraints: None,
            snapshot: None,
            needs_resync: true,
        })
    }
    pub fn edit_grant(&mut self, id: &str, grant: Grant) -> Result<(), RacpError> {
        let item = self
            .catalog
            .iter()
            .find(|item| item.id == id)
            .ok_or_else(|| RacpError::new("PERMISSIONS_INVALID"))?;
        if item.implementation != "rpc" && grant != Grant::Deny {
            return Err(RacpError::new("PERMISSIONS_INVALID"));
        }
        self.grants.insert(id.to_owned(), grant);
        Ok(())
    }
    pub fn category_enabled(&mut self, category: &str, enabled: bool) -> Result<(), RacpError> {
        if !self.catalog.iter().any(|item| item.category == category) {
            return Err(RacpError::new("PERMISSIONS_INVALID"));
        }
        if enabled {
            self.disabled_categories.remove(category);
        } else {
            self.disabled_categories.insert(category.to_owned());
        }
        // Leaf choices survive category off/on. This does not grant authority.
        Ok(())
    }
    pub fn availability(&self, id: &str) -> AvailabilityState {
        self.snapshot
            .as_ref()
            .and_then(|s| s.availability.get(id))
            .map(|a| a.state)
            .unwrap_or_else(|| {
                if self
                    .catalog
                    .iter()
                    .any(|c| c.id == id && c.implementation == "planned")
                {
                    AvailabilityState::NotImplemented
                } else {
                    AvailabilityState::Unverified
                }
            })
    }
    pub fn replace_snapshot(&mut self, snapshot: StateSnapshot) {
        if let Some(policy) = &snapshot.policy {
            self.grants = self
                .catalog
                .iter()
                .map(|i| (i.id.clone(), Grant::Deny))
                .collect();
            self.grants.extend(policy.grants.clone());
            self.disabled_categories = policy.disabled_categories.iter().cloned().collect();
            self.constraints = Some(policy.constraints.clone());
        }
        self.snapshot = Some(snapshot);
        self.needs_resync = false;
    }
    pub fn apply_event(&mut self, event: StateEvent) -> bool {
        let Some(snapshot) = self.snapshot.as_mut() else {
            self.needs_resync = true;
            return false;
        };
        if self.needs_resync
            || snapshot.boot_id != event.boot_id
            || snapshot.sequence.checked_add(1) != Some(event.sequence)
            || event.policy_revision < snapshot.policy_revision
        {
            self.needs_resync = true;
            return false;
        }
        if let Some(result) = ControlCompletion::from_change(&event.change) {
            snapshot.recent_control_results.push(result);
            while snapshot.recent_control_results.len() > MAX_RECENT_CONTROL_RESULTS {
                snapshot.recent_control_results.remove(0);
            }
        }
        match event.change {
            StateChange::Status { status } => snapshot.status = status,
            StateChange::Activity { activity } => snapshot.activity = activity,
            StateChange::Availability { availability } => snapshot.availability = availability,
            StateChange::PolicyCommitted { policy } => {
                self.grants = self
                    .catalog
                    .iter()
                    .map(|i| (i.id.clone(), Grant::Deny))
                    .collect();
                self.grants.extend(policy.grants.clone());
                self.disabled_categories = policy.disabled_categories.iter().cloned().collect();
                self.constraints = Some(policy.constraints.clone());
                snapshot.policy = Some(policy);
            }
            StateChange::ApprovalPending {
                nonce,
                request_id,
                operation,
                target_digest,
                expires_at,
                target_summary,
            } => snapshot.pending_approvals.push(ApprovalPrompt {
                nonce,
                request_id,
                operation,
                target_digest,
                expires_at,
                target_summary,
            }),
            StateChange::ApprovalFinished { nonce, .. } => {
                snapshot.pending_approvals.retain(|p| p.nonce != nonce)
            }
            _ => (),
        }
        snapshot.sequence = event.sequence;
        snapshot.policy_revision = event.policy_revision;
        true
    }
}

/// Tone of an audit log line: 1 success, 2 info, 3 warning, 4 error.
pub type Tone = i32;

/// One-line summary of a control completion receipt: (receipt id, text, tone).
/// Missing records never mean cleanup success, so anything short of a clean result is a warning.
pub fn completion_line(receipt: &ControlCompletion) -> (String, String, Tone) {
    match receipt {
        ControlCompletion::Cancellation {
            receipt_id,
            operation_id,
            cancel_requested,
            state,
            terminal,
            cleanup_status,
            error_code,
        } => (
            receipt_id.clone(),
            format!(
                "작업 {operation_id} · 취소 신호 {cancel_requested} · 관찰 상태 {state} · 종료 {terminal} · 정리 {cleanup_status}{}",
                error_code.as_ref().map(|code| format!(" · {code}")).unwrap_or_default()
            ),
            if error_code.is_some() { 4 } else { 2 },
        ),
        ControlCompletion::Policy {
            receipt_id,
            revision,
            committed,
            retirement_state,
            failed_resource_ids,
        } => (
            receipt_id.clone(),
            format!(
                "정책 revision {revision} · 저장 {committed} · 회수 {retirement_state} · 실패 자원 {}",
                failed_resource_ids.join(", ")
            ),
            if *committed && failed_resource_ids.is_empty() { 1 } else { 3 },
        ),
        ControlCompletion::Stop {
            receipt_id,
            cleanup_state,
            failed_resource_ids,
        } => (
            receipt_id.clone(),
            format!(
                "Agent 정리 {cleanup_state} · 실패 자원 {}",
                failed_resource_ids.join(", ")
            ),
            if cleanup_state == "complete" && failed_resource_ids.is_empty() { 1 } else { 3 },
        ),
    }
}
