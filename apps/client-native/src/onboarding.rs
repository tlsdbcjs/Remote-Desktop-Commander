//! Filesystem and local helper work runs off the presentation thread. Connection tokens stay in Agent.
use crate::app::Message;
use iced::Task;
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
pub fn refresh() -> Task<Message> {
    if crate::control_target::service() {
        return Task::none();
    }
    Task::perform(
        async {
            tokio::task::spawn_blocking(|| {
                let root =
                    racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
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
                        #[cfg(windows)] { Some(racp_runtime::native_login::status(&root).unwrap_or_else(|error|serde_json::json!({"error":error.code.0,"pending":true,"owned":false}))) }
                        #[cfg(not(windows))] { None }
                    },
                    state: Some(NativeState::load(&root).map_err(|e| e.to_string())?),
                    policy: Some(
                        racp_core::state_schema::load_policy(&root).map_err(|e| e.to_string())?,
                    ),
                    runtime: NativeRuntimeConfig::load(&root).map_err(|e| e.to_string())?,
                    registration_pending: false,
                })
            })
            .await
            .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?
        },
        Message::LocalInfo,
    )
}
pub fn inspect(path: String) -> Task<Message> {
    if crate::control_target::service() {
        return Task::done(Message::ConnectionInfo(Err(
            "SERVICE_MODE_LOCAL_ACTION_DENIED".into(),
        )));
    }
    Task::perform(
        async move {
            tokio::task::spawn_blocking(move || {
                racp_core::inspect_connection(std::path::Path::new(&path), None)
                    .map_err(|e| e.to_string())
            })
            .await
            .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?
        },
        Message::ConnectionInfo,
    )
}
pub fn exit_check(wait: bool) -> Task<Message> {
    if crate::control_target::service() {
        return Task::done(Message::ExitCheck(Ok(true)));
    }
    Task::perform(
        async move {
            tokio::task::spawn_blocking(move || {
                let root =
                    racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
                if !root
                    .join(STATE_FILE)
                    .try_exists()
                    .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?
                {
                    return Ok(true);
                }
                let deadline = std::time::Instant::now()
                    + std::time::Duration::from_secs(if wait { 10 } else { 0 });
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
            })
            .await
            .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?
        },
        Message::ExitCheck,
    )
}
pub fn start_agent() -> Task<Message> {
    if crate::control_target::service() {
        return Task::done(Message::StartResult(Err(
            "SERVICE_MODE_LOCAL_ACTION_DENIED".into(),
        )));
    }
    #[cfg(windows)]
    {
        Task::perform(
            async {
                tokio::task::spawn_blocking(|| {
                    let root =
                        racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
                    racp_runtime::local_agent::start(&root).map_err(|e| e.to_string())
                })
                .await
                .map_err(|_| "EXECUTION_UNKNOWN".to_owned())?
            },
            Message::StartResult,
        )
    }
    #[cfg(not(windows))]
    {
        Task::done(Message::StartResult(Err("NATIVE_PLATFORM_DEFERRED".into())))
    }
}
#[derive(Clone, Copy, Debug)]
pub enum Action {
    Enroll,
    AdmitRuntime,
    ConfigureRuntime,
    ConfigureSettings,
    ConfigureLogin,
}
pub fn action(
    action: Action,
    input: Value,
    cancel: tokio_util::sync::CancellationToken,
) -> Task<Message> {
    if crate::control_target::service() {
        return Task::done(Message::LocalAction(
            action,
            Err("SERVICE_MODE_LOCAL_ACTION_DENIED".into()),
        ));
    }
    #[cfg(windows)]
    {
        Task::perform(
            async move {
                let root =
                    racp_core::state_schema::default_user_root().map_err(|e| e.to_string())?;
                use racp_runtime::local_agent::LocalAgentCommand;
                let command = match action {
                    Action::Enroll => LocalAgentCommand::Enroll,
                    Action::AdmitRuntime => LocalAgentCommand::AdmitRuntime,
                    Action::ConfigureRuntime => LocalAgentCommand::ConfigureRuntime,
                    Action::ConfigureSettings => LocalAgentCommand::ConfigureSettings,
                    Action::ConfigureLogin => LocalAgentCommand::ConfigureLogin,
                };
                racp_runtime::local_agent::invoke(command, input, &root, cancel).await
            },
            move |result| Message::LocalAction(action, result),
        )
    }
    #[cfg(not(windows))]
    {
        let _ = (input, cancel);
        Task::done(Message::LocalAction(
            action,
            Err("NATIVE_PLATFORM_DEFERRED".into()),
        ))
    }
}
