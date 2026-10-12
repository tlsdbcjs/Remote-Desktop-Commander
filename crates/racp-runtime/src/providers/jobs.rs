//! Journal metadata and cancellation of the authenticated principal's own job, never arbitrary PIDs.
use super::Provider;
use futures_util::future::BoxFuture;
use racp_contract::RacpError;
use racp_core::Journal;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
pub(crate) type Executions = Arc<Mutex<BTreeMap<String, crate::dispatch::Execution>>>;
pub(super) struct Jobs {
    journal: Journal,
    tasks: Executions,
    revision: AtomicU64,
}
impl Jobs {
    pub fn new(journal: Journal, tasks: Executions) -> Self {
        Self {
            journal,
            tasks,
            revision: AtomicU64::new(0),
        }
    }
}
impl Provider for Jobs {
    fn policy_changed(
        &self,
        policy: racp_core::policy::PolicySnapshot,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            self.revision.store(policy.revision, Ordering::Release);
            Ok(())
        })
    }
    fn capabilities(&self) -> Vec<Value> {
        vec![
            json!({"name":"jobs","version":"1.0.0","operations":["jobs.observe","jobs.cancel"],"supported":true,"healthy":true,"enabled":true,"installed":true}),
        ]
    }
    fn execute(
        &self,
        r: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        Box::pin(async move {
            if cancel.is_cancelled() {
                return Err(RacpError::new("CANCELLED"));
            }
            let id = r["payload"]["target_operation_id"]
                .as_str()
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let target = self.journal.get(id)?;
            if target.request["device_id"] != r["device_id"]
                || target.request["context"]["principal_id"] != r["context"]["principal_id"]
                || target.request["context"]["workspace_id"] != r["context"]["workspace_id"]
                || target.request["execution_mode"] != "job"
                || id == r["operation_id"].as_str().unwrap_or("")
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            let mut requested = false;
            match r["operation"].as_str() {
                Some("jobs.observe") => {}
                Some("jobs.cancel") => {
                    if !Journal::terminal(&target.state) {
                        if target.request["agent_boot_id"] != r["agent_boot_id"]
                            || target.request["connection_epoch"] != r["connection_epoch"]
                        {
                            return Err(RacpError::new("STALE_CONNECTION"));
                        }
                        let task = self
                            .tasks
                            .lock()
                            .await
                            .get(id)
                            .cloned()
                            .ok_or_else(|| RacpError::new("EXECUTION_UNKNOWN"))?;
                        if cancel.is_cancelled()
                            || r["_local_policy_revision"].as_u64()
                                != Some(self.revision.load(Ordering::Acquire))
                        {
                            return Err(RacpError::new("PERMISSION_DENIED"));
                        }
                        let accepted = task
                            .request_cancel(&self.journal, id, "remote_owned_job")
                            .await?;
                        requested = !Journal::terminal(&accepted.state);
                    }
                }
                _ => return Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
            }
            let observed = self.journal.get(id)?;
            let reported = observed
                .result
                .as_ref()
                .and_then(|v| v["cleanup_status"].as_str())
                .or_else(|| {
                    observed
                        .error
                        .as_ref()
                        .and_then(|v| v["details"]["cleanup_status"].as_str())
                });
            let cleanup = match reported {
                Some("complete" | "partial" | "unknown" | "quarantined") => reported.unwrap(),
                _ => {
                    if Journal::terminal(&observed.state) {
                        "not_reported"
                    } else {
                        "pending"
                    }
                }
            };
            Ok(
                json!({"state":"SUCCEEDED","result":{"target_operation_id":id,"operation":observed.request["operation"],"state":observed.state,"terminal":Journal::terminal(&observed.state),"cancel_requested":requested,"cleanup_status":cleanup,"cleanup_complete":cleanup=="complete","outcome_available":observed.outcome_available&&!observed.publication_blocked,"agent_boot_id":observed.request["agent_boot_id"],"owner":observed.request["context"]["principal_id"],"workspace_id":observed.request["context"]["workspace_id"],"metadata_only":true},"error":null}),
            )
        })
    }
}
