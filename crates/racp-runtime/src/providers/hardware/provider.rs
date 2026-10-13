//! Explicitly enabled, policy-authorized snapshot metadata. No native device open/IO.
use crate::providers::Provider;
use futures_util::future::BoxFuture;
use racp_contract::{
    hardware::{HardwareCaller, HardwareTarget},
    timestamp, RacpError,
};
use racp_core::{
    hardware::{HardwareInterface, HardwareInventory, ObservedInterface},
    AgentSettings, Workspaces,
};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;

type Scan = dyn Fn(&Path, Instant, &CancellationToken) -> Result<Vec<ObservedInterface>, RacpError>
    + Send
    + Sync;
type Work = tokio::task::JoinHandle<Result<Vec<ObservedInterface>, RacpError>>;
struct Snapshot {
    inventory: HardwareInventory,
    entries: Vec<HardwareInterface>,
    caller: Option<HardwareCaller>,
    observed_at: String,
    expires: Instant,
    revision: u64,
}
pub struct Hardware {
    enabled: bool,
    device: String,
    boot: String,
    guards: Workspaces,
    policy: Mutex<(u64, Value)>,
    snapshot: Mutex<Snapshot>,
    scan: Arc<Scan>,
    inflight: tokio::sync::Mutex<Option<Work>>,
    stop: CancellationToken,
    unknown_cleanup: Arc<AtomicBool>,
}
impl Hardware {
    pub fn new(settings: &AgentSettings, boot: &str, enabled: bool) -> Result<Self, RacpError> {
        Self::with_scan(
            settings,
            boot,
            enabled,
            Arc::new(|cwd, deadline, cancel| {
                #[cfg(windows)]
                {
                    crate::platform::windows::hardware::worker::discover(cwd, deadline, cancel)
                }
                #[cfg(not(windows))]
                {
                    let _ = (cwd, deadline, cancel);
                    Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
                }
            }),
        )
    }
    fn with_scan(
        settings: &AgentSettings,
        boot: &str,
        enabled: bool,
        scan: Arc<Scan>,
    ) -> Result<Self, RacpError> {
        Ok(Self {
            enabled,
            device: settings.device_id.clone(),
            boot: boot.into(),
            guards: Workspaces::new(&settings.workspace, &settings.allowed_workspaces)?,
            policy: Mutex::new((0, settings.local_permissions()?)),
            snapshot: Mutex::new(Snapshot {
                inventory: HardwareInventory::new(&settings.device_id, boot)?,
                entries: vec![],
                caller: None,
                observed_at: String::new(),
                expires: Instant::now(),
                revision: 0,
            }),
            scan,
            inflight: Default::default(),
            stop: CancellationToken::new(),
            unknown_cleanup: Arc::new(AtomicBool::new(false)),
        })
    }
    fn active(&self, cancel: &CancellationToken, deadline: Instant) -> Result<(), RacpError> {
        if !self.enabled || !cfg!(windows) {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        if self.unknown_cleanup.load(Ordering::SeqCst) {
            return Err(RacpError::new("CLEANUP_UNKNOWN"));
        }
        if self.stop.is_cancelled() || cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        Ok(())
    }
    fn authorize(&self, request: &Value) -> Result<u64, RacpError> {
        let policy = self
            .policy
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        racp_core::authorize_with_approval(
            &policy.1,
            request,
            false,
            request["_local_approved"] == true,
        )?;
        Ok(policy.0)
    }
    fn clear(&self) -> Result<(), RacpError> {
        let mut snapshot = self
            .snapshot
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        snapshot.inventory.invalidate();
        snapshot.entries.clear();
        snapshot.caller = None;
        Ok(())
    }
    async fn settle(
        &self,
        slot: &mut Option<Work>,
    ) -> Result<Option<Vec<ObservedInterface>>, RacpError> {
        let Some(work) = slot.as_mut() else {
            return Ok(None);
        };
        let result = work.await;
        slot.take();
        match result {
            Ok(result) => result.map(Some),
            Err(_) => {
                self.unknown_cleanup.store(true, Ordering::SeqCst);
                Err(RacpError::new("CLEANUP_UNKNOWN"))
            }
        }
    }
    async fn query(
        &self,
        mut request: Value,
        cancel: CancellationToken,
    ) -> Result<Value, RacpError> {
        let deadline = Instant::now()
            + Duration::from_millis(
                request["remaining_timeout_ms"]
                    .as_u64()
                    .unwrap_or(1)
                    .min(30000),
            );
        self.active(&cancel, deadline)?;
        let operation = request["operation"].as_str().unwrap_or("").to_owned();
        let payload = racp_contract::validate_operation(&operation, request["payload"].clone())?;
        request["payload"] = payload.clone();
        let revision = self.authorize(&request)?;
        let caller = HardwareCaller {
            agent_device_id: self.device.clone(),
            agent_boot_id: self.boot.clone(),
            principal_id: request["context"]["principal_id"]
                .as_str()
                .unwrap_or("")
                .into(),
            workspace_id: request["context"]["workspace_id"]
                .as_str()
                .unwrap_or("default")
                .into(),
        };
        caller.validate()?;
        let directory = self.guards.directory(
            &caller.workspace_id,
            self.guards.root(&caller.workspace_id)?,
        )?;
        if operation == "hardware.enumerate" {
            let mut slot = self.inflight.lock().await;
            if slot.is_some() {
                // A dropped caller leaves its worker tracked here. Reap it before reuse.
                let _ = self.settle(&mut slot).await;
            }
            self.active(&cancel, deadline)?;
            if revision != self.authorize(&request)? {
                return Err(RacpError::new("REVISION_CONFLICT"));
            }
            // Polling provides no hotplug continuity; every scan retires prior targets.
            self.clear()?;
            let operation_cancel = self.stop.child_token();
            let _lifetime = operation_cancel.clone().drop_guard();
            let worker_cancel = operation_cancel.clone();
            let scan = self.scan.clone();
            let unknown = self.unknown_cleanup.clone();
            *slot = Some(tokio::task::spawn_blocking(move || {
                let result = scan(&directory.path, deadline, &worker_cancel);
                if result
                    .as_ref()
                    .is_err_and(|e| matches!(e.code.0, "CLEANUP_UNKNOWN" | "CLEANUP_FAILED"))
                {
                    unknown.store(true, Ordering::SeqCst);
                }
                result
            }));
            // Never abandon a child on cancel/stop. Persist its JoinHandle in the slot.
            tokio::select! {
                result = slot.as_mut().unwrap() => {
                    match result {
                        Ok(rows) => { slot.take(); let rows = rows?;
                            self.active(&cancel, deadline)?;
                            if revision != self.authorize(&request)? { return Err(RacpError::new("REVISION_CONFLICT")); }
                            let mut snapshot = self.snapshot.lock().map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                            snapshot.entries = snapshot.inventory.refresh(rows)?;
                            snapshot.caller = Some(caller.clone()); snapshot.observed_at = timestamp();
                            snapshot.revision = revision;
                            snapshot.expires = Instant::now() + Duration::from_secs(30);
                        }
                        Err(_) => { slot.take(); self.unknown_cleanup.store(true, Ordering::SeqCst); return Err(RacpError::new("CLEANUP_UNKNOWN")); }
                    }
                },
                _ = cancel.cancelled() => { operation_cancel.cancel(); self.settle(&mut slot).await?; return Err(RacpError::new("CANCELLED")); },
                _ = self.stop.cancelled() => { operation_cancel.cancel(); self.settle(&mut slot).await?; return Err(RacpError::new("CANCELLED")); },
            }
        } else if operation != "hardware.inspect" {
            return Err(RacpError::new("OPERATION_UNKNOWN"));
        }
        self.active(&cancel, deadline)?;
        if revision != self.authorize(&request)? {
            return Err(RacpError::new("REVISION_CONFLICT"));
        }
        let snapshot = self
            .snapshot
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        if snapshot.revision != revision {
            return Err(RacpError::new("REVISION_CONFLICT"));
        }
        if snapshot.caller.as_ref() != Some(&caller) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if Instant::now() >= snapshot.expires {
            return Err(RacpError::new("HARDWARE_TARGET_STALE"));
        }
        let value = if operation == "hardware.inspect" {
            let target: HardwareTarget = serde_json::from_value(payload["target"].clone())?;
            target.validate()?;
            let row = snapshot
                .entries
                .iter()
                .find(|r| r.target == target)
                .ok_or_else(|| RacpError::new("HARDWARE_TARGET_STALE"))?;
            json!({"item":public_row(row)})
        } else {
            let limit = payload["limit"].as_u64().unwrap_or(100) as usize;
            let items: Vec<_> = snapshot
                .entries
                .iter()
                .take(limit)
                .map(public_row)
                .collect();
            json!({"items":items,"truncated":snapshot.entries.len()>limit})
        };
        let response = json!({"state":"SUCCEEDED","result":{"metadata":value,"device_id":self.device,"agent_boot_id":self.boot,
            "observed_at":snapshot.observed_at,"consistency":"snapshot_only","identity_continuity":"unverified",
            "connected":false,"collection_supported":false},"error":null});
        let policy = self
            .policy
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        if policy.0 != revision {
            return Err(RacpError::new("REVISION_CONFLICT"));
        }
        let max_bytes = policy.1["constraints"]["max_output_bytes"]
            .as_u64()
            .unwrap_or(0)
            .min(1024 * 1024);
        if serde_json::to_vec(&response)?.len() as u64 > max_bytes {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        Ok(response)
    }
}
fn public_row(row: &HardwareInterface) -> Value {
    json!({"target":row.target,"vendor_id":row.vendor_id,"product_id":row.product_id,"transport":"usb",
        "connected":false,"open_supported":false,"collection_supported":false})
}
impl Provider for Hardware {
    fn capabilities(&self) -> Vec<Value> {
        let supported = cfg!(windows);
        let unknown = self.unknown_cleanup.load(Ordering::SeqCst);
        let enabled = self.enabled && !self.stop.is_cancelled();
        vec![
            json!({"name":"hardware","version":"1.0.0","operations":["hardware.enumerate","hardware.inspect"],
            "installed":supported,"supported":supported,"enabled":enabled,"healthy":supported&&enabled&&!unknown,
            "unavailable_reason":if unknown {json!("HARDWARE_CLEANUP_UNKNOWN")}else if !supported {json!("HARDWARE_PLATFORM_UNAVAILABLE")}
                else if !enabled {json!("HARDWARE_DISCOVERY_DISABLED")}else{Value::Null},
            "attributes":{"metadata_only":true,"device_open":false,"device_read":false}}),
        ]
    }
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        Box::pin(async move { self.query(request, cancel).await })
    }
    fn policy_changed(
        &self,
        policy: racp_core::policy::PolicySnapshot,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            let permissions = policy.permissions()?;
            let mut current = self
                .policy
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            current.0 = current
                .0
                .checked_add(1)
                .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
            current.1 = permissions;
            drop(current);
            self.clear()
        })
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            self.stop.cancel();
            let mut slot = self.inflight.lock().await;
            let _result = self.settle(&mut slot).await;
            self.clear()?;
            if self.unknown_cleanup.load(Ordering::SeqCst) {
                return Err(RacpError::new("CLEANUP_UNKNOWN"));
            }
            // Read/parse/cancel failures carry no resource authority. Unknown
            // native termination or a lost worker is separately latched above.
            Ok(())
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;
    fn settings(root: &Path) -> AgentSettings {
        serde_json::from_value(json!({"version":2,"gateway":"http://localhost:1234","device_id":"dev_fixture",
            "workspace":root,"data_dir":root.join("data"),"profile":"trusted_personal","allowed_workspaces":[],
            "ca_file":null,"desktop_enabled":false,"permissions":{"version":1,"grants":{"hardware.inventory.read":"allow"},
            "disabled_categories":[],"constraints":{"workspace_ids":[],"max_timeout_ms":30000,"max_output_bytes":16777216,
            "executable_allowlist":[],"executable_denylist":[],"strict_os_isolation":false}}})).unwrap()
    }
    fn request(op: &str, payload: Value, principal: &str) -> Value {
        json!({"operation":op,"payload":payload,"timeout_ms":30000,"remaining_timeout_ms":30000,
            "operation_id":racp_contract::new_id("op"),"context":{"principal_id":principal,"workspace_id":"default"}})
    }
    fn rows() -> Vec<ObservedInterface> {
        vec![ObservedInterface {
            device_key: "fixture-device-secret".into(),
            interface_key: "fixture-path-secret".into(),
            vendor_id: Some(0x1234),
            product_id: Some(0x5678),
        }]
    }
    #[tokio::test]
    async fn disabled_permission_cancel_and_raw_selector_denials_never_call_backend() {
        let root = tempfile::tempdir().unwrap();
        let settings = settings(root.path());
        let calls = Arc::new(AtomicUsize::new(0));
        let count = calls.clone();
        let scan: Arc<Scan> = Arc::new(move |_, _, _| {
            count.fetch_add(1, Ordering::SeqCst);
            Ok(rows())
        });
        let disabled = Hardware::with_scan(&settings, "boot_fixture", false, scan.clone()).unwrap();
        let req = request("hardware.enumerate", json!({}), "owner_one");
        assert_eq!(
            disabled
                .execute(req.clone(), CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "CAPABILITY_UNAVAILABLE"
        );
        let provider = Hardware::with_scan(&settings, "boot_fixture", true, scan).unwrap();
        let token = CancellationToken::new();
        token.cancel();
        assert_eq!(
            provider
                .execute(req.clone(), token)
                .await
                .unwrap_err()
                .code
                .0,
            "CANCELLED"
        );
        assert!(provider
            .execute(
                request(
                    "hardware.enumerate",
                    json!({"device_path":"forbidden"}),
                    "owner_one"
                ),
                CancellationToken::new()
            )
            .await
            .is_err());
        provider
            .policy_changed(racp_core::state_schema::NativePolicy::deny_all())
            .await
            .unwrap();
        assert_eq!(
            provider
                .execute(req, CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "PERMISSION_DENIED"
        );
        assert_eq!(calls.load(Ordering::SeqCst), 0);
    }
    #[tokio::test]
    async fn metadata_is_scoped_and_never_exposes_backend_locators_or_connection_authority() {
        let root = tempfile::tempdir().unwrap();
        let provider = Hardware::with_scan(
            &settings(root.path()),
            "boot_fixture",
            true,
            Arc::new(|_, _, _| Ok(rows())),
        )
        .unwrap();
        let result = provider
            .execute(
                request("hardware.enumerate", json!({}), "owner_one"),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        let raw = result.to_string();
        assert!(!raw.contains("fixture-device-secret") && !raw.contains("fixture-path-secret"));
        assert_eq!(result["result"]["connected"], false);
        let target = result["result"]["metadata"]["items"][0]["target"].clone();
        let inspect = request("hardware.inspect", json!({"target":target}), "owner_one");
        assert_eq!(
            provider
                .execute(inspect.clone(), CancellationToken::new())
                .await
                .unwrap()["state"],
            "SUCCEEDED"
        );
        assert_eq!(
            provider
                .execute(
                    request("hardware.inspect", json!({"target":target}), "other_owner"),
                    CancellationToken::new()
                )
                .await
                .unwrap_err()
                .code
                .0,
            "PERMISSION_DENIED"
        );
        let mut foreign = target.clone();
        foreign["agent_boot_id"] = json!("boot_foreign");
        assert_eq!(
            provider
                .execute(
                    request("hardware.inspect", json!({"target":foreign}), "owner_one"),
                    CancellationToken::new()
                )
                .await
                .unwrap_err()
                .code
                .0,
            "HARDWARE_TARGET_STALE"
        );
        provider
            .execute(
                request("hardware.enumerate", json!({}), "owner_one"),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        assert_eq!(
            provider
                .execute(inspect, CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "HARDWARE_TARGET_STALE"
        );
        provider.cleanup().await.unwrap();
    }
    #[tokio::test]
    async fn dropped_query_stays_tracked_until_stop_reaps_the_worker() {
        let root = tempfile::tempdir().unwrap();
        let exited = Arc::new(AtomicBool::new(false));
        let done = exited.clone();
        let provider = Hardware::with_scan(
            &settings(root.path()),
            "boot_fixture",
            true,
            Arc::new(move |_, _, cancel| {
                while !cancel.is_cancelled() {
                    std::thread::sleep(Duration::from_millis(1));
                }
                done.store(true, Ordering::SeqCst);
                Err(RacpError::new("CANCELLED"))
            }),
        )
        .unwrap();
        assert!(tokio::time::timeout(
            Duration::from_millis(20),
            provider.execute(
                request("hardware.enumerate", json!({}), "owner_one"),
                CancellationToken::new()
            )
        )
        .await
        .is_err());
        provider.cleanup().await.unwrap();
        assert!(exited.load(Ordering::SeqCst));
        assert!(!provider.capabilities()[0]["healthy"].as_bool().unwrap());
    }
    #[tokio::test]
    async fn unknown_worker_cleanup_blocks_reuse_and_empty_inventory_cannot_hide_it() {
        let root = tempfile::tempdir().unwrap();
        let provider = Hardware::with_scan(
            &settings(root.path()),
            "boot_fixture",
            true,
            Arc::new(|_, _, _| Err(RacpError::new("CLEANUP_UNKNOWN"))),
        )
        .unwrap();
        let req = request("hardware.enumerate", json!({}), "owner_one");
        assert_eq!(
            provider
                .execute(req.clone(), CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "CLEANUP_UNKNOWN"
        );
        assert_eq!(
            provider
                .execute(req, CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "CLEANUP_UNKNOWN"
        );
        assert_eq!(
            provider.cleanup().await.unwrap_err().code.0,
            "CLEANUP_UNKNOWN"
        );
    }
    #[tokio::test]
    async fn response_respects_local_output_budget_without_returning_partial_data() {
        let root = tempfile::tempdir().unwrap();
        let mut config = settings(root.path());
        config.permissions.as_mut().unwrap()["constraints"]["max_output_bytes"] = json!(1024);
        let provider = Hardware::with_scan(
            &config,
            "boot_fixture",
            true,
            Arc::new(|_, _, _| {
                Ok((0..10)
                    .map(|i| ObservedInterface {
                        device_key: format!("fixture-device-{i}"),
                        interface_key: format!("fixture-interface-{i}"),
                        vendor_id: None,
                        product_id: None,
                    })
                    .collect())
            }),
        )
        .unwrap();
        assert_eq!(
            provider
                .execute(
                    request("hardware.enumerate", json!({}), "owner_one"),
                    CancellationToken::new()
                )
                .await
                .unwrap_err()
                .code
                .0,
            "RESOURCE_EXHAUSTED"
        );
        let result = provider
            .execute(
                request("hardware.enumerate", json!({"limit":1}), "owner_one"),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        assert_eq!(
            result["result"]["metadata"]["items"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(result["result"]["metadata"]["truncated"], true);
        provider.cleanup().await.unwrap();
    }
    #[tokio::test]
    async fn late_success_cannot_publish_an_expired_snapshot() {
        let root = tempfile::tempdir().unwrap();
        let provider = Hardware::with_scan(
            &settings(root.path()),
            "boot_fixture",
            true,
            Arc::new(|_, _, _| {
                std::thread::sleep(Duration::from_millis(40));
                Ok(rows())
            }),
        )
        .unwrap();
        let mut req = request("hardware.enumerate", json!({}), "owner_one");
        req["remaining_timeout_ms"] = json!(10);
        assert_eq!(
            provider
                .execute(req, CancellationToken::new())
                .await
                .unwrap_err()
                .code
                .0,
            "TIMEOUT"
        );
        assert!(provider.snapshot.lock().unwrap().entries.is_empty());
        provider.cleanup().await.unwrap();
    }
    #[tokio::test]
    async fn policy_revocation_during_scan_rejects_late_metadata() {
        let root = tempfile::tempdir().unwrap();
        let entered = Arc::new(tokio::sync::Notify::new());
        let released = Arc::new(AtomicBool::new(false));
        let start = entered.clone();
        let release = released.clone();
        let provider = Arc::new(
            Hardware::with_scan(
                &settings(root.path()),
                "boot_fixture",
                true,
                Arc::new(move |_, _, cancel| {
                    start.notify_one();
                    while !release.load(Ordering::SeqCst) && !cancel.is_cancelled() {
                        std::thread::sleep(Duration::from_millis(1));
                    }
                    Ok(rows())
                }),
            )
            .unwrap(),
        );
        let running = provider.clone();
        let work = tokio::spawn(async move {
            running
                .execute(
                    request("hardware.enumerate", json!({}), "owner_one"),
                    CancellationToken::new(),
                )
                .await
        });
        entered.notified().await;
        provider
            .policy_changed(racp_core::state_schema::NativePolicy::deny_all())
            .await
            .unwrap();
        released.store(true, Ordering::SeqCst);
        assert_eq!(work.await.unwrap().unwrap_err().code.0, "PERMISSION_DENIED");
        assert!(provider.snapshot.lock().unwrap().entries.is_empty());
        provider.cleanup().await.unwrap();
    }
}
