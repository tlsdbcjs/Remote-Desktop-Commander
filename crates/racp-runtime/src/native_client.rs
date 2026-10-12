//! Native enrollment/run paths never load legacy credentials or loopback control.
use racp_contract::{digest, local_ipc::*, RacpError};
use racp_core::{state_schema::*, WorkspaceSpec};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};
use tokio_util::sync::CancellationToken;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Enrollment {
    pub connection_file: PathBuf,
    pub connection_sha256: String,
    pub workspace: PathBuf,
    pub allowed_workspaces: Vec<WorkspaceSpec>,
    pub profile: String,
    pub policy: NativePolicy,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeAdmission {
    pub manifest_file: PathBuf,
    pub manifest_sha256: String,
    pub source_directory: PathBuf,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeConfiguration {
    pub expected_revision: u64,
    pub config: racp_core::runtime_admission::NativeRuntimeConfig,
}
/// Local maintenance only, with no remote operation or arbitrary executable/argv binding.
pub async fn runtime_command(
    action: &str,
    raw: &[u8],
    root: &Path,
    shutdown: CancellationToken,
) -> Result<Value, RacpError> {
    if raw.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    reject_duplicate_keys(raw)?;
    let raw = raw.to_owned();
    let root = root.to_owned();
    let action = action.to_owned();
    let cancelled = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let signal = cancelled.clone();
    let mut work = tokio::task::spawn_blocking(move || {
        NativeState::load(&root)?;
        let budget = racp_core::runtime_admission::AdmissionBudget {
            deadline: std::time::Instant::now() + std::time::Duration::from_secs(120),
            cancelled,
        };
        match action.as_str() {
            #[cfg(windows)]
            "native-login-configure" => {
                let input: crate::native_login::Update = serde_json::from_slice(&raw)?;
                if budget.cancelled.load(std::sync::atomic::Ordering::SeqCst) {
                    return Err(RacpError::new("CANCELLED"));
                }
                crate::native_login::configure(&root, input)
            }
            "native-settings-configure" => {
                let input: NativeSettingsUpdate = serde_json::from_slice(&raw)?;
                if budget.cancelled.load(std::sync::atomic::Ordering::SeqCst) {
                    return Err(RacpError::new("CANCELLED"));
                }
                NativeState::configure(&root, input)
            }
            "native-runtime-admit" => {
                let input: RuntimeAdmission = serde_json::from_slice(&raw)?;
                let manifest = racp_core::read_bounded(&input.manifest_file, 1048576, false)?;
                let admitted = racp_core::runtime_admission::admit(
                    &root,
                    &input.source_directory,
                    &manifest,
                    &input.manifest_sha256,
                    &budget,
                )?;
                Ok(
                    json!({"admitted":true,"activated":false,"manifest_sha256":input.manifest_sha256.to_ascii_lowercase(),"runtime_id":admitted.runtime.id,"version":admitted.runtime.version,"trust_basis":"explicit_local_manifest_hash"}),
                )
            }
            "native-runtime-configure" => {
                let input: RuntimeConfiguration = serde_json::from_slice(&raw)?;
                input.config.save(&root, input.expected_revision, &budget)?;
                Ok(json!({"committed":true,"revision":input.config.revision,"requires_start":true}))
            }
            _ => Err(RacpError::new("REQUEST_INVALID")),
        }
    });
    tokio::select! {result=&mut work=>result.map_err(|_|RacpError::new("EXECUTION_UNKNOWN"))?,_=shutdown.cancelled()=>{signal.store(true,std::sync::atomic::Ordering::SeqCst);work.await.map_err(|_|RacpError::new("EXECUTION_UNKNOWN"))?}}
}
#[cfg(windows)]
async fn prepare_runtime(
    root: &Path,
    shutdown: &CancellationToken,
) -> Result<crate::runtime_catalog::Prepared, RacpError> {
    crate::runtime_catalog::prepare(root, shutdown).await
}
pub async fn enroll(raw: &[u8], root: &Path) -> Result<Value, RacpError> {
    enroll_inner(raw, root, false).await
}
#[cfg(windows)]
pub async fn enroll_service(raw: &[u8], root: &Path) -> Result<Value, RacpError> {
    let config = racp_core::native_service::NativeServiceConfig::load_inputs(root)?;
    super::local_ipc::LocalEndpoint::for_service(root, config.scope)?;
    enroll_inner(raw, root, true).await
}
async fn enroll_inner(raw: &[u8], root: &Path, service: bool) -> Result<Value, RacpError> {
    if raw.is_empty() || raw.len() > MAX_FRAME_BYTES {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    reject_duplicate_keys(raw)?;
    let request: Enrollment = serde_json::from_slice(raw)?;
    if request.connection_sha256.len() != 64
        || !request
            .connection_sha256
            .bytes()
            .all(|v| v.is_ascii_hexdigit())
        || request.policy.revision != 0
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let (connection, _) = racp_core::load_connection(
        &request.connection_file,
        Some(&request.connection_sha256.to_ascii_lowercase()),
    )?;
    let ca = connection
        .ca_pem
        .as_ref()
        .map(|pem| root.join(format!("native-ca-{}.pem", digest(pem))));
    let mut state = NativeState {
        schema_version: STATE_SCHEMA_VERSION,
        settings_revision: 0,
        gateway: connection.gateway.clone(),
        device_id: "dev_pending".into(),
        workspace: request.workspace,
        allowed_workspaces: request.allowed_workspaces,
        profile: request.profile,
        ca_file: None,
    };
    // Validate workspace/grants/limits before state reservation and before token consumption.
    state.agent_settings(root, &request.policy)?;
    racp_core::SecretStore::probe_current_account()?;
    #[cfg(windows)]
    let reservation = if service {
        NativeRegistration::begin_service(root)?
    } else {
        NativeRegistration::begin(root)?
    };
    #[cfg(not(windows))]
    let reservation = {
        let _ = service;
        NativeRegistration::begin(root)?
    };
    if let (Some(path), Some(pem)) = (&ca, &connection.ca_pem) {
        racp_core::atomic_write(path, pem.as_bytes(), false)?;
        state.ca_file = ca;
    }
    let client = crate::http_client(state.ca_file.as_deref())?;
    let mut response = client
        .post(format!("{}/agent/v1/enroll", state.gateway))
        .json(&json!({"token":connection.token}))
        .send()
        .await
        .map_err(|e| crate::network::network_error(&e))?;
    if response.status().as_u16() != 200 {
        if matches!(response.status().as_u16(), 401 | 403) {
            if let (Some(path), Some(pem)) = (&state.ca_file, &connection.ca_pem) {
                if racp_core::read_bounded(path, 16_384, false)? != pem.as_bytes() {
                    return Err(RacpError::new("REGISTRATION_STORAGE_FAILED"));
                }
                std::fs::remove_file(path)?;
            }
            reservation.rejected()?;
            return Err(RacpError::new("TOKEN_REJECTED"));
        }
        return Err(RacpError::new("REGISTRATION_OUTCOME_UNKNOWN"));
    }
    let mut raw = vec![];
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|e| crate::network::network_error(&e))?
    {
        if raw.len() + chunk.len() > 4096 {
            return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
        }
        raw.extend_from_slice(&chunk);
    }
    reject_duplicate_keys(&raw)?;
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Registered {
        device_id: String,
        credential: String,
    }
    let result: Registered = serde_json::from_slice(&raw)?;
    state.device_id = result.device_id;
    let device_id = state.device_id.clone();
    reservation.commit(&state, &result.credential, &request.policy)?;
    Ok(json!({"device_id":device_id,"schema_version":STATE_SCHEMA_VERSION,"registered":true}))
}
#[cfg(windows)]
pub async fn run(root: &Path, shutdown: CancellationToken) -> Result<(), RacpError> {
    super::local_ipc::LocalEndpoint::for_root(root)?;
    run_inner(root, shutdown, None, None).await
}
#[cfg(windows)]
pub async fn run_service(
    root: &Path,
    shutdown: CancellationToken,
    ready: tokio::sync::oneshot::Sender<()>,
) -> Result<(), RacpError> {
    racp_core::verify_private_native_directory(root)?;
    let _config_lock =
        racp_core::InstanceLock::acquire(&root.join(racp_core::native_service::CONFIG_LOCK))?;
    let config = racp_core::native_service::NativeServiceConfig::load(root)?;
    let endpoint = super::local_ipc::LocalEndpoint::for_service(root, config.scope.clone())?;
    super::local_ipc::allow_service_identity_queries(&config.scope)?;
    let directory = racp_core::service_directory::create(&config.endpoint_dir, &config.scope)?;
    run_inner(
        root,
        shutdown,
        Some((config, endpoint, directory)),
        Some(ready),
    )
    .await
}
#[cfg(windows)]
async fn run_inner(
    root: &Path,
    shutdown: CancellationToken,
    service: Option<(
        racp_core::native_service::NativeServiceConfig,
        super::local_ipc::LocalEndpoint,
        racp_core::Directory,
    )>,
    ready: Option<tokio::sync::oneshot::Sender<()>>,
) -> Result<(), RacpError> {
    racp_core::verify_private_native_directory(root)?;
    let _lock = racp_core::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    let state = NativeState::load(root)?;
    let policy = load_policy(root)?;
    let settings = state.agent_settings(root, &policy)?;
    let credential = state.credential(root)?;
    let mut lifecycle = racp_core::native_lifecycle::NativeRun::begin(root, &state)?;
    racp_core::private_dir(&settings.data_dir)?;
    // The pin retains Windows read-only/no-delete handles until Agent and IPC cleanup complete.
    let prepared = prepare_runtime(root, &shutdown).await?;
    let runtime_pin = prepared.chromium;
    let runtime_config = prepared.config;
    let runtime_error = prepared.errors.get("chromium").copied();
    let missing_cdb = prepared.providers.cdb.is_none();
    let missing_gdb = prepared.providers.gdb.is_none();
    let missing_analysis = prepared.providers.analysis.is_none();
    if shutdown.is_cancelled() {
        return Err(RacpError::new("CANCELLED"));
    }
    let missing_browser = runtime_pin.is_none() && !runtime_config.external_cdp;
    let browser = crate::providers::browser::BrowserConfig {
        executable: runtime_pin.as_ref().map(|pin| pin.entrypoint.clone()),
        allow_origins: runtime_config.browser_allow_origins,
        cdp_enabled: runtime_config.external_cdp,
    };
    // Native default does not discover or implicitly start bundled/installed Chromium.
    let mut agent = crate::Agent::with_runtimes(settings, credential, browser, prepared.providers)?;
    lifecycle.running(&agent.boot_id)?;
    agent.native_settings_revision = Some(state.settings_revision);
    let catalog: Vec<Value> = serde_json::from_str(include_str!(
        "../../../packages/protocol/src/racp_protocol/permission_catalog.json"
    ))?;
    let availability: BTreeMap<String, Availability> = catalog
        .iter()
        .map(|item| {
            let planned = item["implementation"] == "planned";
            let service_desktop_missing = service.is_some()
                && item["operations"].as_array().is_some_and(|operations| {
                    !operations.is_empty()
                        && operations.iter().all(|operation| {
                            operation.as_str().is_some_and(|operation| {
                                operation.starts_with("desktop.")
                                    || operation.starts_with("clipboard.")
                            })
                        })
                });
            let missing_browser_runtime = !planned
                && missing_browser
                && item["id"]
                    .as_str()
                    .is_some_and(|id| id.starts_with("browser."));
            let needs_runtime = missing_browser_runtime
                || !planned
                    && item["operations"].as_array().is_some_and(|operations| {
                        !operations.is_empty()
                            && operations.iter().all(|operation| {
                                let operation = operation.as_str().unwrap_or("");
                                missing_cdb
                                    && ["native.prepare", "native.start", "native.close"]
                                        .contains(&operation)
                                    || missing_gdb
                                        && operation.starts_with("debugger.")
                                        && operation != "debugger.backends"
                                    || missing_analysis
                                        && operation.starts_with("re.")
                                        && operation != "re.backends"
                            })
                    });
            (
                item["id"].as_str().unwrap().to_owned(),
                Availability {
                    state: if planned {
                        AvailabilityState::NotImplemented
                    } else if needs_runtime || service_desktop_missing {
                        AvailabilityState::NeedsRuntime
                    } else {
                        AvailabilityState::Unverified
                    },
                    reason: if planned {
                        "NOT_IMPLEMENTED"
                    } else if service_desktop_missing {
                        "NATIVE_SERVICE_SESSION_NOT_CONNECTED"
                    } else if needs_runtime {
                        if missing_browser_runtime {
                            runtime_error.unwrap_or("BROWSER_RUNTIME_NOT_CONFIGURED")
                        } else {
                            "OPTIONAL_NATIVE_RUNTIME_NOT_CONFIGURED"
                        }
                    } else {
                        "NATIVE_ACCEPTANCE_DEFERRED"
                    }
                    .into(),
                },
            )
        })
        .collect();
    let hub = super::local_ipc::events::StateHub::new(StateSnapshot {
        boot_id: agent.boot_id.clone(),
        sequence: 0,
        policy_revision: policy.revision,
        status: AgentStatus::Offline,
        activity: agent
            .journal
            .recent_activity(&agent.settings.device_id, 32)?,
        availability,
        policy: Some(PolicyView {
            revision: policy.revision,
            grants: policy.grants.clone(),
            disabled_categories: policy.disabled_categories.iter().cloned().collect(),
            constraints: policy.constraints.clone(),
        }),
        pending_approvals: vec![],
        recent_control_results: vec![],
        identity: Some(AgentIdentity {
            device_id: agent.settings.device_id.clone(),
            gateway_origin: racp_core::gateway_origin(&agent.settings.gateway)?,
            execution_scope: if service.is_some() {
                ExecutionScope::Service
            } else {
                ExecutionScope::User
            },
        }),
    })?;
    agent.native_state = Some(hub.clone());
    agent.policy = Some(racp_core::policy::PolicyStore::open(root)?);
    agent.providers.policy_changed(policy).await?;
    // Await both branches after cancellation so IPC failure cannot drop Agent cleanup.
    let (agent_result, ipc_result) = tokio::join!(
        async {
            let result = agent.run(shutdown.clone()).await;
            shutdown.cancel();
            result
        },
        async {
            let controller = super::local_ipc::policy::ControlPlane::new(
                agent.clone(),
                hub.clone(),
                shutdown.clone(),
            );
            let result = if let Some((config, endpoint, _directory)) = service {
                super::local_ipc::serve_endpoint(
                    &config.endpoint_dir,
                    endpoint,
                    hub,
                    shutdown.clone(),
                    controller,
                    ready,
                )
                .await
            } else {
                super::local_ipc::serve(root, hub, shutdown.clone(), controller).await
            };
            shutdown.cancel();
            result
        }
    );
    lifecycle.finish(agent_result)?;
    agent_result?;
    ipc_result
}
