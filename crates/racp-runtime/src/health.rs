//! Fixed runtime diagnostics; weak views cannot create Agent/Provider ownership cycles.
use crate::{dispatch::Execution, resources::ResourceObserver};
use racp_contract::RacpError;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, AtomicU8, Ordering},
        Arc, Mutex, Weak,
    },
    time::Instant,
};
use tokio::sync::{Mutex as AsyncMutex, RwLock};

#[derive(Clone, Copy)]
pub(crate) enum CleanupPhase {
    NotRequested = 0,
    InProgress = 1,
    Complete = 2,
    Unknown = 3,
}
pub(crate) struct LifecycleHealth {
    started: Instant,
    cleanup: AtomicU8,
    last_error: Mutex<Option<&'static str>>,
}
impl Default for LifecycleHealth {
    fn default() -> Self {
        Self {
            started: Instant::now(),
            cleanup: AtomicU8::new(CleanupPhase::NotRequested as u8),
            last_error: Mutex::new(None),
        }
    }
}
impl LifecycleHealth {
    pub(crate) fn cleanup(&self, phase: CleanupPhase) {
        self.cleanup.store(phase as u8, Ordering::Release);
    }
    pub(crate) fn error(&self, code: &str) {
        // Never retain an arbitrary upstream message or raw error code.
        let safe = match code {
            "TOKEN_REJECTED" => "TOKEN_REJECTED",
            "GATEWAY_TIMEOUT" => "GATEWAY_TIMEOUT",
            "GATEWAY_UNREACHABLE" => "GATEWAY_UNREACHABLE",
            "TLS_FAILED" => "TLS_FAILED",
            "CLEANUP_FAILED" | "CLEANUP_UNKNOWN" => "CLEANUP_UNKNOWN",
            "LOCAL_STATE_FAILED" | "LOCAL_STATE_BUSY" => "LOCAL_STATE_FAILED",
            _ => "RUNTIME_ERROR",
        };
        if let Ok(mut last) = self.last_error.lock() {
            *last = Some(safe);
        }
    }
    pub(crate) fn snapshot(&self) -> Value {
        let cleanup = match self.cleanup.load(Ordering::Acquire) {
            0 => "not_requested",
            1 => "in_progress",
            2 => "complete",
            _ => "unknown",
        };
        json!({"agent_uptime_seconds":self.started.elapsed().as_secs(),"cleanup_phase":cleanup,
            "last_error_code":self.last_error.lock().ok().map(|last|*last).unwrap_or(Some("LOCAL_STATE_FAILED"))})
    }
}

#[derive(Clone)]
pub(crate) struct HealthSource {
    pub(crate) journal: racp_core::Journal,
    pub(crate) lifecycle: Arc<LifecycleHealth>,
    pub(crate) status: Weak<RwLock<Value>>,
    pub(crate) tasks: Weak<AsyncMutex<BTreeMap<String, Execution>>>,
    pub(crate) lease: Weak<AsyncMutex<tokio::time::Instant>>,
    pub(crate) lease_retiring: Weak<AtomicBool>,
    pub(crate) stopping: Weak<AtomicBool>,
    pub(crate) resources: ResourceObserver,
}
impl HealthSource {
    pub(crate) fn sample(&self) -> Result<Value, RacpError> {
        let unavailable = || RacpError::new("DIAGNOSTICS_UNAVAILABLE");
        let busy = |_| RacpError::new("LOCAL_STATE_BUSY");
        let status = self.status.upgrade().ok_or_else(unavailable)?;
        let status = status.try_read().map_err(busy)?;
        let state = match status["state"].as_str() {
            Some("RUNNING") => "RUNNING",
            Some("STOPPED") => "STOPPED",
            _ => "unknown",
        };
        let phase = match status["connection_phase"].as_str() {
            Some("starting") => "starting",
            Some("connecting") => "connecting",
            Some("hello") => "hello",
            Some("welcome") => "welcome",
            Some("reconcile") => "reconcile",
            Some("ready") => "ready",
            Some("disconnected") => "disconnected",
            Some("stopped") => "stopped",
            _ => "unknown",
        };
        let connected = status["connected"] == true;
        let tasks = self.tasks.upgrade().ok_or_else(unavailable)?;
        let active = tasks.try_lock().map_err(busy)?.len();
        let lease = self.lease.upgrade().ok_or_else(unavailable)?;
        let lease_valid = tokio::time::Instant::now() < *lease.try_lock().map_err(busy)?;
        let retiring = self
            .lease_retiring
            .upgrade()
            .ok_or_else(unavailable)?
            .load(Ordering::Acquire);
        let stopping = self
            .stopping
            .upgrade()
            .ok_or_else(unavailable)?
            .load(Ordering::Acquire);
        let lifecycle = self.lifecycle.snapshot();
        Ok(
            json!({"observed":true,"consistency":"sampled","observed_at":racp_contract::timestamp(),
            "runtime_state":state,"gateway_phase":phase,"gateway_connected":connected,
            "execution_lease_valid":connected && phase=="ready" && lease_valid && !retiring && !stopping && lifecycle["cleanup_phase"]=="not_requested",
            "stopping":stopping,"active_operations":active,"resources":self.resources.counts()?,
            "lifecycle":lifecycle}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{providers::Provider, Agent};
    use futures_util::future::BoxFuture;
    use tokio_util::sync::CancellationToken;
    fn agent(root: &std::path::Path) -> Agent {
        let workspace = root.join("private-workspace-name");
        std::fs::create_dir_all(&workspace).unwrap();
        let settings = serde_json::from_value(json!({"version":2,"gateway":"http://localhost:1234","device_id":"dev_health","workspace":workspace,"data_dir":root.join("private-state-path"),"profile":"trusted_personal","allowed_workspaces":[],"ca_file":null,"desktop_enabled":false,
            "permissions":{"version":1,"grants":{"diagnostics.agent.read":"allow"},"disabled_categories":[],"constraints":{"workspace_ids":[],"max_timeout_ms":60000,"max_output_bytes":1048576,"executable_allowlist":[],"executable_denylist":[],"strict_os_isolation":false}}})).unwrap();
        Agent::new(settings, "private-credential-must-not-appear".repeat(2)).unwrap()
    }
    fn request() -> Value {
        json!({"operation":"diagnostics.agent","payload":{},"remaining_timeout_ms":1000})
    }
    async fn diagnostics(agent: &Agent) -> Value {
        agent
            .providers
            .execute(request(), CancellationToken::new())
            .await
            .unwrap()["result"]
            .clone()
    }
    #[tokio::test]
    async fn diagnostics_observes_live_lease_and_redacts_internal_state() {
        let root = tempfile::tempdir().unwrap();
        let agent = agent(root.path());
        {
            let mut status = agent.status.write().await;
            status["connected"] = json!(true);
            status["connection_phase"] = json!("ready");
            status["command_line"] = json!("private-command-arguments");
            status["token"] = json!("private-token");
        }
        *agent.lease.lock().await =
            tokio::time::Instant::now() + std::time::Duration::from_secs(10);
        agent.health.error("private-upstream-error");
        let result = diagnostics(&agent).await;
        assert_eq!(result["agent_boot_id"], agent.boot_id);
        assert_eq!(result["health"]["execution_lease_valid"], true);
        assert_eq!(
            result["health"]["lifecycle"]["last_error_code"],
            "RUNTIME_ERROR"
        );
        assert_eq!(
            result["health"]["lifecycle"]["cleanup_phase"],
            "not_requested"
        );
        let bytes = serde_json::to_string(&result).unwrap();
        assert!(bytes.len() < 8192);
        for secret in [
            "private-command-arguments",
            "private-token",
            "private-credential",
            "private-state-path",
            "private-workspace-name",
            "private-upstream-error",
            "http://localhost:1234",
        ] {
            assert!(!bytes.contains(secret));
        }
        *agent.lease.lock().await = tokio::time::Instant::now();
        assert_eq!(
            diagnostics(&agent).await["health"]["execution_lease_valid"],
            false
        );
        *agent.lease.lock().await =
            tokio::time::Instant::now() + std::time::Duration::from_secs(10);
        agent.lease_retiring.store(true, Ordering::Release);
        assert_eq!(
            diagnostics(&agent).await["health"]["execution_lease_valid"],
            false
        );
        agent.lease_retiring.store(false, Ordering::Release);
        agent.stopping.store(true, Ordering::Release);
        assert_eq!(
            diagnostics(&agent).await["health"]["execution_lease_valid"],
            false
        );
    }
    #[tokio::test]
    async fn diagnostics_refuses_busy_state_expired_budget_and_detached_agent() {
        let root = tempfile::tempdir().unwrap();
        let agent = agent(root.path());
        let held = agent.status.write().await;
        assert_eq!(
            agent
                .providers
                .execute(request(), CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "LOCAL_STATE_BUSY"
        );
        drop(held);
        let mut expired = request();
        expired["remaining_timeout_ms"] = json!(0);
        assert_eq!(
            agent
                .providers
                .execute(expired, CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "TIMEOUT"
        );
        let cancelled = CancellationToken::new();
        cancelled.cancel();
        assert_eq!(
            agent
                .providers
                .execute(request(), cancelled)
                .await
                .unwrap_err()
                .code
                .0,
            "CANCELLED"
        );
        let providers = agent.providers.clone();
        let status = Arc::downgrade(&agent.status);
        drop(agent);
        assert!(status.upgrade().is_none());
        assert_eq!(
            providers
                .execute(request(), CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "DIAGNOSTICS_UNAVAILABLE"
        );
    }
    struct CleanupFixture(bool);
    impl Provider for CleanupFixture {
        fn capabilities(&self) -> Vec<Value> {
            vec![]
        }
        fn execute(
            &self,
            _: Value,
            _: CancellationToken,
        ) -> BoxFuture<'_, Result<Value, RacpError>> {
            Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) })
        }
        fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
            Box::pin(async move {
                if self.0 {
                    Err(RacpError::new("CLEANUP_UNKNOWN"))
                } else {
                    Ok(())
                }
            })
        }
    }
    #[tokio::test]
    async fn shutdown_diagnosis_distinguishes_confirmed_cleanup_from_unknown() {
        for failed in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let mut agent = agent(root.path());
            agent.providers = Arc::new(CleanupFixture(failed));
            let stop = CancellationToken::new();
            stop.cancel();
            let outcome = agent.run(stop).await;
            let snapshot = agent.snapshot().await;
            if failed {
                assert_eq!(outcome.unwrap_err().code.0, "CLEANUP_UNKNOWN");
                assert_eq!(snapshot["health"]["cleanup_phase"], "unknown");
                assert_eq!(snapshot["health"]["last_error_code"], "CLEANUP_UNKNOWN");
                assert_ne!(snapshot["state"], "STOPPED");
            } else {
                outcome.unwrap();
                assert_eq!(snapshot["health"]["cleanup_phase"], "complete");
                assert_eq!(snapshot["state"], "STOPPED");
            }
        }
    }
    #[tokio::test]
    async fn resource_counts_retain_unconfirmed_cleanup_without_private_ids_or_requests() {
        use crate::resources::{Ownership, Resource, ResourceKind, ResourceScope, RetirementScope};
        let root = tempfile::tempdir().unwrap();
        let agent = agent(root.path());
        let release = Arc::new(tokio::sync::Notify::new());
        let signal = release.clone();
        agent
            .resources
            .register(Resource {
                id: "private-resource-id".into(),
                scope: ResourceScope {
                    device_id: agent.settings.device_id.clone(),
                    boot_id: agent.boot_id.clone(),
                    epoch: 1,
                    workspace_id: "default".into(),
                    policy_revision: 1,
                },
                kind: ResourceKind::Session,
                ownership: Ownership::Borrowed,
                terminates_target: false,
                request: json!({"secret":"private-resource-payload"}),
                retire: Arc::new(move || {
                    let signal = signal.clone();
                    Box::pin(async move {
                        signal.notified().await;
                        Ok(())
                    })
                }),
            })
            .await
            .unwrap();
        let before = diagnostics(&agent).await;
        assert_eq!(before["health"]["resources"]["borrowed"], 1);
        let report = agent
            .resources
            .retire(
                RetirementScope::default(),
                std::time::Duration::from_millis(10),
            )
            .await;
        assert_eq!(report.failed_resource_ids.len(), 1);
        let pending = diagnostics(&agent).await;
        assert_eq!(pending["health"]["resources"]["cleanup_unconfirmed"], 1);
        assert_eq!(pending["health"]["resources"]["active"], 0);
        assert!(!serde_json::to_string(&pending)
            .unwrap()
            .contains("private-resource"));
        release.notify_one();
        let report = agent
            .resources
            .retire(
                RetirementScope::default(),
                std::time::Duration::from_secs(1),
            )
            .await;
        assert!(report.failed_resource_ids.is_empty());
        assert_eq!(
            diagnostics(&agent).await["health"]["resources"]["tracked"],
            0
        );
    }
}
