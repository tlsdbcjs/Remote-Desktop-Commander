//! Fixed, bounded Agent maintenance helpers for the local native GUI. No remote binding.
use crate::providers::containment::{CommandSpec, OwnedProcess};
use racp_contract::{local_ipc::reject_duplicate_keys, RacpError};
use racp_core::Workspaces;
use serde_json::Value;
use std::{
    io::Write,
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
#[derive(Clone, Copy, Debug)]
pub enum LocalAgentCommand {
    Enroll,
    AdmitRuntime,
    ConfigureRuntime,
    ConfigureSettings,
    ConfigureLogin,
}
impl LocalAgentCommand {
    fn action(self) -> &'static str {
        match self {
            Self::Enroll => "native-enroll",
            Self::AdmitRuntime => "native-runtime-admit",
            Self::ConfigureRuntime => "native-runtime-configure",
            Self::ConfigureSettings => "native-settings-configure",
            Self::ConfigureLogin => "native-login-configure",
        }
    }
}
pub async fn invoke(
    action: LocalAgentCommand,
    input: Value,
    root: &Path,
    cancel: CancellationToken,
) -> Result<Value, String> {
    let root = root.to_owned();
    let input = serde_json::to_vec(&input).map_err(|_| "REQUEST_INVALID".to_owned())?;
    if input.len() > 65535 {
        return Err("REQUEST_INVALID".into());
    }
    let cancelled = Arc::new(AtomicBool::new(false));
    let signal = cancelled.clone();
    let mut work = tokio::task::spawn_blocking(move || run(action, &input, &root, &cancelled));
    let result = tokio::select! {result=&mut work=>result,_=cancel.cancelled()=>{signal.store(true,Ordering::SeqCst);work.await}};
    result.map_err(|_| "EXECUTION_UNKNOWN".to_owned())?
}
fn run(
    action: LocalAgentCommand,
    input: &[u8],
    root: &Path,
    cancel: &AtomicBool,
) -> Result<Value, String> {
    fn inner(
        action: LocalAgentCommand,
        input: &[u8],
        root: &Path,
        cancel: &AtomicBool,
    ) -> Result<Value, RacpError> {
        racp_core::validate_local_path(root)?;
        let current = std::env::current_exe()?;
        let directory = current
            .parent()
            .ok_or_else(|| RacpError::new("AGENT_EXECUTABLE_MISSING"))?;
        #[cfg(windows)]
        let name = "racp-agent.exe";
        #[cfg(not(windows))]
        let name = "racp-agent";
        let guards = Workspaces::new(directory, &[])?;
        let cwd = guards.directory("default", directory)?;
        let mut exe_pin = cwd.open_pinned_read(name)?;
        racp_core::runtime_admission::validate_host_binary(&mut exe_pin)?;
        let (mut child, mut stdin) = OwnedProcess::spawn_stdio(CommandSpec {
            argv: vec![
                directory.join(name).to_string_lossy().into_owned(),
                action.action().into(),
                "--state-dir".into(),
                root.to_string_lossy().into_owned(),
            ],
            environment: crate::providers::containment::environment(&serde_json::json!({}))?,
            cwd,
        })?;
        #[cfg(windows)]
        child.limit_memory(256 * 1024 * 1024)?;
        let _protected = crate::identity::ProtectedProcess::register(child.pid())?;
        stdin.write_all(input)?;
        stdin.write_all(b"\n")?;
        drop(stdin);
        let deadline = Instant::now() + Duration::from_secs(125);
        let mut stdout = vec![];
        let mut stderr = vec![];
        let mut block = [0u8; 4096];
        let result = (|| loop {
            if cancel.load(Ordering::SeqCst) {
                return Err(RacpError::new("LOCAL_AGENT_OUTCOME_UNKNOWN"));
            }
            if Instant::now() >= deadline {
                return Err(RacpError::new("TIMEOUT"));
            }
            for (pipe, data) in [
                (&mut child.stdout, &mut stdout),
                (&mut child.stderr, &mut stderr),
            ] {
                for _ in 0..4 {
                    match pipe.read_available(&mut block)? {
                        Some(n) if n > 0 => {
                            if data.len() + n > 65536 {
                                return Err(RacpError::new("OUTPUT_LIMIT_EXCEEDED"));
                            }
                            data.extend_from_slice(&block[..n]);
                        }
                        _ => break,
                    }
                }
            }
            if let Some(code) = child.poll()? {
                for _ in 0..16 {
                    match child.stdout.read_available(&mut block)? {
                        Some(n) if n > 0 => {
                            if stdout.len() + n > 65536 {
                                return Err(RacpError::new("OUTPUT_LIMIT_EXCEEDED"));
                            }
                            stdout.extend_from_slice(&block[..n]);
                        }
                        _ => break,
                    }
                }
                reject_duplicate_keys(&stdout)?;
                let value: Value = serde_json::from_slice(&stdout)?;
                if code != 0 || value["ok"] != true {
                    const CODES: &[&str] = &[
                        "SETTINGS_BUSY",
                        "REGISTRATION_OUTCOME_UNKNOWN",
                        "REGISTRATION_STORAGE_FAILED",
                        "REGISTRATION_PENDING",
                        "TOKEN_REJECTED",
                        "GATEWAY_UNREACHABLE",
                        "GATEWAY_TIMEOUT",
                        "TLS_FAILED",
                        "CA_INVALID",
                        "CONNECTION_FILE_EXPIRED",
                        "CONNECTION_FILE_INVALID",
                        "RUNTIME_MANIFEST_UNTRUSTED",
                        "RUNTIME_FILE_MISMATCH",
                        "RUNTIME_BINARY_INVALID",
                        "RUNTIME_PLATFORM_MISMATCH",
                        "RUNTIME_MANIFEST_INVALID",
                        "RUNTIME_CONFIG_INVALID",
                        "STATE_REVISION_CONFLICT",
                        "STATE_SCHEMA_UNSUPPORTED",
                        "STATE_ALREADY_EXISTS",
                        "STATE_DIRECTORY_NOT_PRIVATE",
                        "LOCAL_AGENT_OUTCOME_UNKNOWN",
                        "WORKSPACE_INVALID",
                        "LOGIN_OUTCOME_UNKNOWN",
                        "LOGIN_OWNERSHIP_CONFLICT",
                        "LOGIN_ACCESS_DENIED",
                        "PACKAGE_INVALID",
                        "SETTINGS_INVALID",
                        "PERMISSION_DENIED",
                        "PATH_ACCESS_DENIED",
                        "CANCELLED",
                        "TIMEOUT",
                    ];
                    let reported = value["error"]["code"].as_str().unwrap_or("");
                    return Err(RacpError::new(
                        CODES
                            .iter()
                            .copied()
                            .find(|code| *code == reported)
                            .unwrap_or("LOCAL_AGENT_REQUEST_FAILED"),
                    ));
                }
                return Ok(value["result"].clone());
            }
            std::thread::sleep(Duration::from_millis(10));
        })();
        child.kill_tree()?;
        let cleanup = Instant::now() + Duration::from_secs(5);
        while !child.tree_empty()? {
            if Instant::now() >= cleanup {
                return Err(RacpError::new("CLEANUP_UNKNOWN"));
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        result
    }
    inner(action, input, root, cancel).map_err(|e| e.to_string())
}
/// The Agent is a separately owned user daemon. It must remain outside the GUI's helper Jobs;
/// its Guardian survives Broker/Agent failure to release input. Stop uses authenticated IPC.
#[cfg(windows)]
pub fn start(root: &Path) -> Result<Value, RacpError> {
    use std::os::windows::process::CommandExt;
    racp_core::state_schema::NativeState::load(root)?;
    let reservation = racp_core::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    let current = std::env::current_exe()?;
    let directory = current
        .parent()
        .ok_or_else(|| RacpError::new("AGENT_EXECUTABLE_MISSING"))?;
    let guard = Workspaces::new(directory, &[])?;
    let cwd = guard.directory("default", directory)?;
    let mut pin = cwd.open_pinned_read("racp-agent.exe")?;
    racp_core::runtime_admission::validate_host_binary(&mut pin)?;
    let mut environment = crate::providers::containment::environment(&serde_json::json!({}))?;
    if let Some(value) = std::env::var_os("LOCALAPPDATA") {
        environment.insert("LOCALAPPDATA".into(), value.to_string_lossy().into_owned());
    }
    let child = std::process::Command::new(directory.join("racp-agent.exe"))
        .args(["native-run", "--state-dir"])
        .arg(root)
        .current_dir(directory)
        .env_clear()
        .envs(environment)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .creation_flags(0x01000000 | 0x08000000 | 0x00000200)
        .spawn()?;
    let pid = child.id();
    drop(reservation);
    // Dropping the parent handle does not terminate the independent native Agent.
    drop(child);
    Ok(
        serde_json::json!({"spawned":true,"pid":pid,"state":"starting","ready":false,"control":"authenticated_named_pipe"}),
    )
}
