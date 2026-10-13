//! Local registration state and Agent helper commands. Callers run these off the UI thread.
//! Connection tokens stay in the Agent helper.
use racp_core::{
    runtime_admission::NativeRuntimeConfig,
    state_schema::{NativePolicy, NativeState, STATE_FILE},
};
use serde_json::Value;
#[derive(Clone, Debug)]
pub struct LocalInfo {
    pub state: Option<NativeState>,
    pub policy: Option<NativePolicy>,
    pub runtime: NativeRuntimeConfig,
    pub registration_pending: bool,
    pub login: Option<Value>,
}
/// Reads local registration, policy, runtime and login state. Blocking; call off the UI thread.
pub fn load_local() -> Result<LocalInfo, String> {
    let root = racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
    let registered = root
        .join(STATE_FILE)
        .try_exists()
        .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?;
    if !registered {
        return Ok(LocalInfo {
            state: None,
            policy: None,
            runtime: NativeRuntimeConfig::default(),
            login: None,
            registration_pending: root
                .join("native-registration.pending")
                .try_exists()
                .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?,
        });
    }
    Ok(LocalInfo {
        login: {
            #[cfg(windows)]
            {
                Some(racp_runtime::native_login::status(&root).unwrap_or_else(
                    |error| serde_json::json!({"error":error.code.0,"pending":true,"owned":false}),
                ))
            }
            #[cfg(not(windows))]
            {
                None
            }
        },
        state: Some(NativeState::load(&root).map_err(|e| e.to_string())?),
        policy: Some(racp_core::state_schema::load_policy(&root).map_err(|e| e.to_string())?),
        runtime: NativeRuntimeConfig::load(&root).map_err(|e| e.to_string())?,
        registration_pending: false,
    })
}
/// Validates a `.racp` connection file and returns its token-free preview. Blocking.
pub fn inspect_file(path: &str) -> Result<Value, String> {
    if crate::control_target::service() {
        return Err("SERVICE_MODE_LOCAL_ACTION_DENIED".into());
    }
    racp_core::inspect_connection(std::path::Path::new(path), None).map_err(|e| e.to_string())
}
/// Reports whether no registered Agent is running or it stopped cleanly. Blocking.
pub fn stopped_cleanly(wait: bool) -> Result<bool, String> {
    let root = racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
    if !root
        .join(STATE_FILE)
        .try_exists()
        .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?
    {
        return Ok(true);
    }
    let deadline =
        std::time::Instant::now() + std::time::Duration::from_secs(if wait { 10 } else { 0 });
    loop {
        match racp_core::native_lifecycle::stopped_cleanly(&root) {
            Ok(clean) => return Ok(clean),
            Err(error) if error.code.0 == "SETTINGS_BUSY" => {
                if std::time::Instant::now() >= deadline {
                    return Ok(false);
                }
            }
            Err(error) => return Err(error.to_string()),
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}
/// Starts the registered user Agent. Blocking.
#[cfg(windows)]
pub fn start_local_agent() -> Result<Value, String> {
    if crate::control_target::service() {
        return Err("SERVICE_MODE_LOCAL_ACTION_DENIED".into());
    }
    let root = racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
    racp_runtime::local_agent::start(&root).map_err(|e| e.to_string())
}
/// Runs one local Agent helper command. Connection tokens stay inside the Agent helper.
#[cfg(windows)]
pub async fn run_action(
    action: Action,
    input: Value,
    cancel: tokio_util::sync::CancellationToken,
) -> Result<Value, String> {
    // A service-mode GUI never changes local registration, settings or runtime selection.
    if crate::control_target::service() {
        return Err("SERVICE_MODE_LOCAL_ACTION_DENIED".into());
    }
    let root = racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
    use racp_runtime::local_agent::LocalAgentCommand;
    let command = match action {
        Action::Enroll => LocalAgentCommand::Enroll,
        Action::AdmitRuntime => LocalAgentCommand::AdmitRuntime,
        Action::ConfigureRuntime => LocalAgentCommand::ConfigureRuntime,
        Action::ConfigureSettings => LocalAgentCommand::ConfigureSettings,
        Action::ConfigureLogin => LocalAgentCommand::ConfigureLogin,
    };
    racp_runtime::local_agent::invoke(command, input, &root, cancel).await
}
#[derive(Clone, Copy, Debug)]
pub enum Action {
    Enroll,
    AdmitRuntime,
    ConfigureRuntime,
    ConfigureSettings,
    ConfigureLogin,
}
