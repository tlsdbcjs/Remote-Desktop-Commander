//! Fixed, bounded Agent maintenance helpers for the local native GUI. No remote binding.
use crate::providers::containment::{CommandSpec, OwnedProcess};
use racp_contract::{local_ipc::reject_duplicate_keys, RacpError};
use racp_core::Workspaces;
use serde_json::Value;
use std::{
    fs::File,
    io::Write,
    path::Path,
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
    let _guard = CancelOnDrop(cancel.child_token());
    let cancelled = _guard.0.clone();
    let mut work = tokio::task::spawn_blocking(move || run(action, &input, &root, &cancelled));
    let result = tokio::select! {result=&mut work=>result,_=cancel.cancelled()=>{_guard.0.cancel();work.await}};
    result.map_err(|_| "EXECUTION_UNKNOWN".to_owned())?
}
fn run(
    action: LocalAgentCommand,
    input: &[u8],
    root: &Path,
    cancel: &CancellationToken,
) -> Result<Value, String> {
    fn inner(
        action: LocalAgentCommand,
        input: &[u8],
        root: &Path,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        check_budget(cancel, Instant::now() + Duration::from_secs(125))?;
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
        let (mut child, stdin) = OwnedProcess::spawn_stdio(CommandSpec {
            argv: vec![
                directory.join(name).to_string_lossy().into_owned(),
                action.action().into(),
                "--state-dir".into(),
                root.to_string_lossy().into_owned(),
            ],
            environment: crate::providers::containment::environment(&serde_json::json!({}))?,
            cwd,
        })?;
        let deadline = Instant::now() + Duration::from_secs(125);
        let stdout = run_owned(&mut child, stdin, input, deadline, cancel, |child| {
            #[cfg(windows)]
            child.limit_memory(256 * 1024 * 1024)?;
            crate::identity::ProtectedProcess::register(child.pid()).map(Some)
        })?;
        let (code, stdout) = stdout;
        reject_duplicate_keys(&stdout)?;
        let value: Value = serde_json::from_slice(&stdout)?;
        check_budget(cancel, deadline)?;
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
        Ok(value["result"].clone())
    }
    inner(action, input, root, cancel).map_err(|e| e.to_string())
}
struct CancelOnDrop(CancellationToken);
impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.cancel();
    }
}
fn check_budget(cancel: &CancellationToken, deadline: Instant) -> Result<(), RacpError> {
    if cancel.is_cancelled() {
        return Err(RacpError::new("LOCAL_AGENT_OUTCOME_UNKNOWN"));
    }
    if Instant::now() >= deadline {
        return Err(RacpError::new("TIMEOUT"));
    }
    Ok(())
}
// All paths after spawn converge here, including setup and stdin failures.
// The writer owns only our input pipe; terminating the owned child releases a
// blocked write. Confirm its exit too before reporting the helper outcome.
fn run_owned(
    child: &mut OwnedProcess,
    mut stdin: File,
    input: &[u8],
    deadline: Instant,
    cancel: &CancellationToken,
    setup: impl FnOnce(&OwnedProcess) -> Result<Option<crate::identity::ProtectedProcess>, RacpError>,
) -> Result<(i64, Vec<u8>), RacpError> {
    let mut protected = None;
    let mut writer = None;
    let result = (|| {
        check_budget(cancel, deadline)?;
        protected = setup(child)?;
        let input = input.to_vec();
        let (tx, rx) = std::sync::mpsc::channel();
        writer = Some(
            std::thread::Builder::new()
                .name("racp-helper-input".into())
                .spawn(move || {
                    let result = stdin.write_all(&input).and_then(|_| stdin.write_all(b"\n"));
                    drop(stdin);
                    let _ = tx.send(result);
                })?,
        );
        loop {
            check_budget(cancel, deadline)?;
            match rx.recv_timeout(Duration::from_millis(10)) {
                Ok(result) => {
                    result?;
                    break;
                }
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                Err(_) => return Err(RacpError::new("LOCAL_AGENT_OUTCOME_UNKNOWN")),
            }
        }
        super::providers::worker::capture_child(child, 65536, 65536, deadline, cancel).map_err(
            |error| match error.code.0 {
                "CANCELLED" => RacpError::new("LOCAL_AGENT_OUTCOME_UNKNOWN"),
                "RESOURCE_EXHAUSTED" => RacpError::new("OUTPUT_LIMIT_EXCEEDED"),
                _ => error,
            },
        )
    })();
    let result = super::providers::worker::finish_owned(child, result);
    if let Some(writer) = writer {
        let until = Instant::now() + Duration::from_secs(5);
        while !writer.is_finished() {
            if Instant::now() >= until {
                return Err(RacpError::new("CLEANUP_UNKNOWN"));
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        writer
            .join()
            .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))?;
    }
    result
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::BTreeMap, io::Read};

    #[test]
    #[ignore = "owned helper fixture; invoked only by local helper tests"]
    fn helper_fixture() {
        match std::env::var("RACP_LOCAL_HELPER_FIXTURE").as_deref() {
            Ok("no_read") => std::thread::sleep(Duration::from_secs(30)),
            Ok("stderr") => {
                std::io::stdin().read_to_end(&mut Vec::new()).unwrap();
                std::io::stderr().write_all(&vec![b'e'; 65537]).unwrap();
            }
            _ => {
                let mut input = Vec::new();
                std::io::stdin().read_to_end(&mut input).unwrap();
                std::io::stdout().write_all(&vec![b'x'; 60000]).unwrap();
            }
        }
        std::process::exit(0);
    }
    fn fixture(root: &Path, mode: &str) -> (OwnedProcess, File) {
        OwnedProcess::spawn_stdio(CommandSpec {
            argv: vec![
                std::env::current_exe()
                    .unwrap()
                    .to_string_lossy()
                    .into_owned(),
                "--ignored".into(),
                "--exact".into(),
                "local_agent::tests::helper_fixture".into(),
                "--nocapture".into(),
            ],
            environment: BTreeMap::from([("RACP_LOCAL_HELPER_FIXTURE".into(), mode.into())]),
            cwd: Workspaces::new(root, &[])
                .unwrap()
                .directory("default", root)
                .unwrap(),
        })
        .unwrap()
    }
    fn exited(child: &mut OwnedProcess) {
        assert!(child.poll().unwrap().is_some());
        assert!(child.tree_empty().unwrap());
    }
    #[test]
    fn blocked_helper_input_obeys_timeout_and_cancel() {
        let root = tempfile::tempdir().unwrap();
        for cancelled in [false, true] {
            let (mut child, stdin) = fixture(root.path(), "no_read");
            let cancel = CancellationToken::new();
            let signal = cancel.clone();
            let trigger = cancelled.then(|| {
                std::thread::spawn(move || {
                    std::thread::sleep(Duration::from_millis(100));
                    signal.cancel();
                })
            });
            let started = Instant::now();
            let result = run_owned(
                &mut child,
                stdin,
                &vec![b'i'; 65535],
                started + Duration::from_millis(400),
                &cancel,
                |_| Ok(None),
            );
            assert_eq!(
                result.unwrap_err().code.0,
                if cancelled {
                    "LOCAL_AGENT_OUTCOME_UNKNOWN"
                } else {
                    "TIMEOUT"
                }
            );
            if let Some(trigger) = trigger {
                trigger.join().unwrap();
            }
            assert!(started.elapsed() < Duration::from_secs(10));
            exited(&mut child);
        }
    }
    #[test]
    fn helper_streams_are_drained_and_late_overflow_is_rejected() {
        let root = tempfile::tempdir().unwrap();
        for mode in ["large", "stderr"] {
            let (mut child, stdin) = fixture(root.path(), mode);
            let result = run_owned(
                &mut child,
                stdin,
                b"{}",
                Instant::now() + Duration::from_secs(10),
                &CancellationToken::new(),
                |_| Ok(None),
            );
            if mode == "large" {
                let (code, stdout) = result.unwrap();
                assert_eq!(code, 0);
                assert_eq!(stdout.iter().filter(|b| **b == b'x').count(), 60000);
                assert!(child.stdout.eof() && child.stderr.eof());
            } else {
                assert_eq!(result.unwrap_err().code.0, "OUTPUT_LIMIT_EXCEEDED");
            }
            exited(&mut child);
        }
    }
    #[test]
    fn helper_setup_failure_still_confirms_owned_exit() {
        let root = tempfile::tempdir().unwrap();
        let (mut child, stdin) = fixture(root.path(), "no_read");
        let result = run_owned(
            &mut child,
            stdin,
            b"{}",
            Instant::now() + Duration::from_secs(10),
            &CancellationToken::new(),
            |_| Err(RacpError::new("PERMISSION_DENIED")),
        );
        assert_eq!(result.unwrap_err().code.0, "PERMISSION_DENIED");
        exited(&mut child);
    }
    #[tokio::test]
    async fn dropped_call_cancels_worker_and_precancel_rejects_before_executable_lookup() {
        let caller = CancellationToken::new();
        let guard = CancelOnDrop(caller.child_token());
        let worker = guard.0.clone();
        drop(guard);
        assert!(worker.is_cancelled());
        assert!(!caller.is_cancelled());
        caller.cancel();
        let result = invoke(
            LocalAgentCommand::Enroll,
            serde_json::json!({}),
            Path::new("missing-fixture-root"),
            caller,
        )
        .await;
        assert!(result.unwrap_err().contains("LOCAL_AGENT_OUTCOME_UNKNOWN"));
    }
}
