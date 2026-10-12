use super::events::StateHub;
use crate::{resources::RetirementScope, Agent};
use racp_contract::{local_ipc::*, new_id, RacpError};
use std::{
    sync::{atomic::Ordering, Arc},
    time::Duration,
};
use tokio::sync::Semaphore;
use tokio_util::sync::CancellationToken;

#[derive(Clone)]
pub struct ControlPlane {
    agent: Agent,
    hub: StateHub,
    shutdown: CancellationToken,
    workers: Arc<Semaphore>,
}
impl ControlPlane {
    #[cfg(windows)]
    pub async fn desktop_session(
        &self,
        command: ControlCommand,
        peer: crate::providers::desktop::PeerIdentity,
        scope: Option<&racp_contract::service_scope::ServiceScope>,
    ) -> Result<ControlResult, RacpError> {
        use racp_contract::service_scope::ControllerRole;
        let scope = scope.ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        if scope.role(&peer.sid, peer.session) != Some(ControllerRole::Controller)
            || self.shutdown.is_cancelled()
            || self.agent.stopping.load(Ordering::Acquire)
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let _serial = self.agent.policy_updates.lock().await;
        if self.shutdown.is_cancelled() || self.agent.stopping.load(Ordering::Acquire) {
            return Err(RacpError::new("DEVICE_OFFLINE"));
        }
        match command {
            ControlCommand::RemoveDesktop { boot_id, pair_id } if boot_id == self.agent.boot_id => {
                self.agent
                    .providers
                    .remove_desktop(peer, pair_id.clone())
                    .await?;
                let mut availability = self.hub.snapshot().await.availability;
                let live = self
                    .agent
                    .providers
                    .capabilities()
                    .iter()
                    .any(|cap| cap["name"] == "desktop" && cap["healthy"] == true);
                if !live {
                    for (id, item) in &mut availability {
                        if (id.starts_with("desktop.") || id.starts_with("clipboard."))
                            && item.state != AvailabilityState::NotImplemented
                        {
                            item.state = AvailabilityState::NeedsRuntime;
                            item.reason = "NATIVE_SERVICE_SESSION_NOT_CONNECTED".into();
                        }
                    }
                }
                let (revision, _) = self.agent.current_permissions()?;
                self.hub
                    .publish(revision, StateChange::Availability { availability })
                    .await?;
                Ok(ControlResult::DesktopRemoved { pair_id })
            }
            ControlCommand::PrepareDesktop { boot_id } if boot_id == self.agent.boot_id => {
                Ok(ControlResult::DesktopPrepared {
                    seed: self.agent.providers.prepare_desktop(peer).await?,
                })
            }
            ControlCommand::RegisterDesktop {
                boot_id,
                pair_id,
                broker_pid,
                broker_created,
                guardian_pid,
                guardian_created,
            } if boot_id == self.agent.boot_id => {
                let session_id = peer.session;
                self.agent
                    .providers
                    .register_desktop(
                        peer,
                        pair_id.clone(),
                        broker_pid,
                        broker_created,
                        guardian_pid,
                        guardian_created,
                    )
                    .await?;
                let mut availability = self.hub.snapshot().await.availability;
                for (id, item) in &mut availability {
                    if (id.starts_with("desktop.") || id.starts_with("clipboard."))
                        && item.reason == "NATIVE_SERVICE_SESSION_NOT_CONNECTED"
                    {
                        item.state = AvailabilityState::Unverified;
                        item.reason = "NATIVE_ACCEPTANCE_DEFERRED".into();
                    }
                }
                let (revision, _) = self.agent.current_permissions()?;
                self.hub
                    .publish(revision, StateChange::Availability { availability })
                    .await?;
                Ok(ControlResult::DesktopRegistered {
                    pair_id,
                    session_id,
                })
            }
            _ => Err(RacpError::new("STALE_CONNECTION")),
        }
    }
    pub fn new(agent: Agent, hub: StateHub, shutdown: CancellationToken) -> Self {
        Self {
            agent,
            hub,
            shutdown,
            workers: Arc::new(Semaphore::new(16)),
        }
    }
    pub async fn apply(&self, command: ControlCommand) -> Result<ControlResult, RacpError> {
        if self.shutdown.is_cancelled() || self.agent.stopping.load(Ordering::Acquire) {
            return Err(RacpError::new("DEVICE_OFFLINE"));
        }
        match command {
            ControlCommand::CancelOperation {
                boot_id,
                operation_id,
                target_request_id,
            } => {
                if boot_id != self.agent.boot_id {
                    return Err(RacpError::new("STALE_CONNECTION"));
                }
                let permit = self
                    .workers
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
                let receipt_id = new_id("cancel");
                let receipt = receipt_id.clone();
                let controller = self.clone();
                tokio::spawn(async move {
                    let _permit = permit;
                    let change = super::cancellation::observe(
                        &controller.agent,
                        &boot_id,
                        &operation_id,
                        &target_request_id,
                        receipt,
                    )
                    .await;
                    // Policy commits may occur during the observation wait.
                    let serial = controller.agent.policy_updates.lock().await;
                    let revision = controller
                        .agent
                        .current_permissions()
                        .map(|(r, _)| r)
                        .unwrap_or(0);
                    let _ = controller.hub.publish(revision, change).await;
                    drop(serial);
                });
                Ok(ControlResult::Accepted { receipt_id })
            }
            ControlCommand::DecideApproval { nonce, allow } => {
                let (revision, _) = self.agent.current_permissions()?;
                self.agent.approvals.decide(&nonce, allow, revision)?;
                Ok(ControlResult::Accepted {
                    receipt_id: new_id("approval_decision"),
                })
            }
            ControlCommand::UpdatePolicy {
                expected_revision,
                changes,
            } => {
                let permit = self
                    .workers
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
                let receipt_id = new_id("policy");
                let receipt = receipt_id.clone();
                let controller = self.clone();
                tokio::spawn(async move {
                    let _permit = permit;
                    let result = controller.update(expected_revision, changes).await;
                    let (revision, committed, state, failed) = match result {
                        Ok(receipt) => (
                            receipt.revision,
                            receipt.committed,
                            receipt.retirement_state,
                            receipt.failed_resource_ids,
                        ),
                        Err(error) => (
                            controller
                                .agent
                                .current_permissions()
                                .map(|(r, _)| r)
                                .unwrap_or(expected_revision),
                            false,
                            error.code.0.into(),
                            vec![],
                        ),
                    };
                    let _ = controller
                        .hub
                        .publish(
                            revision,
                            StateChange::PolicyFinished {
                                receipt_id: receipt,
                                revision,
                                committed,
                                retirement_state: state,
                                failed_resource_ids: failed,
                            },
                        )
                        .await;
                });
                Ok(ControlResult::Accepted { receipt_id })
            }
            ControlCommand::Stop => {
                let receipt_id = new_id("stop");
                // Stop is initiated after the receipt is sent by the pipe host.
                Ok(ControlResult::Accepted { receipt_id })
            }
            _ => Err(RacpError::new("REQUEST_INVALID")),
        }
    }
    pub async fn stop_after_receipt(&self, receipt_id: String) {
        let controller = self.clone();
        tokio::spawn(async move {
            let _serial = controller.agent.policy_updates.lock().await;
            {
                let _emission = controller.agent.emission_gate.write().await;
                controller.agent.stopping.store(true, Ordering::Release);
            }
            let revision = controller
                .agent
                .current_permissions()
                .map(|(r, _)| r)
                .unwrap_or(0);
            let _ = controller
                .hub
                .publish(
                    revision,
                    StateChange::Status {
                        status: AgentStatus::Draining,
                    },
                )
                .await;
            controller
                .agent
                .policy_retiring
                .store(true, Ordering::Release);
            controller.agent.approvals.invalidate();
            let _ = controller.agent.cancel_all("shutdown").await;
            let report = controller
                .agent
                .resources
                .retire(RetirementScope::default(), Duration::from_secs(15))
                .await;
            let drain = async {
                loop {
                    let notified = controller.agent.completed.notified();
                    if controller.agent.tasks.lock().await.is_empty() {
                        break;
                    }
                    notified.await;
                }
            };
            let drained = tokio::time::timeout(Duration::from_secs(15), drain)
                .await
                .is_ok();
            let cleanup = tokio::time::timeout(
                Duration::from_secs(15),
                controller.agent.providers.cleanup(),
            )
            .await;
            let mut failed = report.failed_resource_ids;
            if !drained {
                failed.push("executions".into());
            }
            if !matches!(cleanup, Ok(Ok(()))) {
                failed.push("providers".into());
            }
            let _ = controller
                .hub
                .publish(
                    revision,
                    StateChange::StopFinished {
                        receipt_id,
                        cleanup_state: if failed.is_empty() {
                            "complete"
                        } else {
                            "failed"
                        }
                        .into(),
                        failed_resource_ids: failed,
                    },
                )
                .await;
            controller.shutdown.cancel();
        });
    }
    async fn update(
        &self,
        expected: u64,
        changes: PolicyChanges,
    ) -> Result<racp_core::policy::PolicyReceipt, RacpError> {
        let _serial = self.agent.policy_updates.lock().await;
        if self.agent.stopping.load(Ordering::Acquire) {
            return Err(RacpError::new("DEVICE_OFFLINE"));
        }
        let store = self
            .agent
            .policy
            .clone()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        self.agent.policy_retiring.store(true, Ordering::Release);
        struct Gate(Arc<std::sync::atomic::AtomicBool>);
        impl Drop for Gate {
            fn drop(&mut self) {
                self.0.store(false, Ordering::Release);
            }
        }
        let _gate = Gate(self.agent.policy_retiring.clone());
        let emission = self.agent.emission_gate.write().await;
        let next = tokio::task::spawn_blocking(move || store.update(expected, changes))
            .await
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))??;
        self.agent.approvals.invalidate();
        let mut failed = vec![];
        match self.agent.journal.records() {
            Ok(records) => {
                for record in records {
                    if !record.publication_blocked
                        && self.agent.authorize_record(&record).is_err()
                        && self.agent.journal.block_publication(&record.id).is_err()
                    {
                        failed.push(format!("journal:{}", record.id));
                    }
                }
            }
            Err(_) => failed.push("journal:publication_retirement".into()),
        }
        drop(emission);
        self.hub
            .publish(
                next.revision,
                StateChange::PolicyCommitted {
                    policy: PolicyView {
                        revision: next.revision,
                        grants: next.grants.clone(),
                        disabled_categories: next.disabled_categories.iter().cloned().collect(),
                        constraints: next.constraints.clone(),
                    },
                },
            )
            .await
            .unwrap_or_else(|_| failed.push("events:policy_commit".into()));
        let mut revoked = vec![];
        // Broker input/Guardian release is requested before awaiting operation cleanup.
        if let Err(error) = self.agent.providers.policy_changed(next.clone()).await {
            failed.push(format!("provider:{}", error.code.0));
        }
        for (id, request) in self.agent.resources.requests().await {
            if self.agent.authorize_request(&request).is_err() {
                revoked.push(id);
            }
        }
        let report = self
            .agent
            .resources
            .retire(
                RetirementScope {
                    before_policy_revision: Some(next.revision),
                    resource_ids: Some(revoked),
                    ..Default::default()
                },
                Duration::from_secs(15),
            )
            .await;
        failed.extend(report.failed_resource_ids);
        self.hub
            .publish(
                next.revision,
                StateChange::Retirement {
                    failed_resource_ids: failed.clone(),
                },
            )
            .await
            .unwrap_or_else(|_| failed.push("events:retirement".into()));
        Ok(racp_core::policy::PolicyReceipt {
            revision: next.revision,
            committed: true,
            retirement_state: if failed.is_empty() {
                "complete"
            } else {
                "quarantined"
            }
            .into(),
            failed_resource_ids: failed,
        })
    }
}
