//! Fixed Rust helpers; callers cannot select an executable or an arbitrary worker entry point.
use super::containment::{CommandSpec, OwnedProcess};
use racp_contract::RacpError;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    io::Write,
    path::Path,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
pub(crate) fn run(
    action: &str,
    cwd: &Path,
    input: Value,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<Value, RacpError> {
    if !["image-preview-worker", "trash-worker", "diagnostic-worker"].contains(&action) {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    let bytes = serde_json::to_vec(&input)?;
    if bytes.len() > 16384 {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let guard = racp_core::Workspaces::new(cwd, &[])?.directory("default", cwd)?;
    let (mut child, mut stdin) = OwnedProcess::spawn_stdio(CommandSpec {
        argv: vec![
            std::env::current_exe()?.to_string_lossy().into_owned(),
            action.into(),
        ],
        environment: BTreeMap::new(),
        cwd: guard,
    })?;
    #[cfg(windows)]
    child.limit_memory(256 * 1024 * 1024)?;
    let _protected = crate::identity::ProtectedProcess::register(child.pid())?;
    stdin.write_all(&bytes)?;
    stdin.write_all(b"\n")?;
    drop(stdin);
    let mut stdout = vec![];
    let mut stderr = vec![];
    let mut buffer = [0u8; 4096];
    let result = (|| loop {
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        for (pipe, data) in [
            (&mut child.stdout, &mut stdout),
            (&mut child.stderr, &mut stderr),
        ] {
            for _ in 0..4 {
                match pipe.read_available(&mut buffer)? {
                    Some(n) if n > 0 => {
                        if data.len() + n > 16384 {
                            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                        }
                        data.extend_from_slice(&buffer[..n]);
                    }
                    _ => break,
                }
            }
        }
        if let Some(code) = child.poll()? {
            for _ in 0..4 {
                if let Some(n) = child.stdout.read_available(&mut buffer)? {
                    if stdout.len() + n > 16384 {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    stdout.extend_from_slice(&buffer[..n]);
                }
            }
            racp_contract::local_ipc::reject_duplicate_keys(&stdout)?;
            let result: Value = serde_json::from_slice(&stdout)?;
            if code != 0 || result["ok"] != true {
                return Err(RacpError::new("EXECUTION_UNKNOWN"));
            }
            return Ok(result["result"].clone());
        }
        std::thread::sleep(Duration::from_millis(5));
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
