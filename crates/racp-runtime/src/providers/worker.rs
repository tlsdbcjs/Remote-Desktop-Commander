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
fn cleanup_child(
    deadline: Instant,
    mut parent: impl FnMut() -> Result<Option<i64>, RacpError>,
    mut tree_empty: impl FnMut() -> Result<bool, RacpError>,
) -> Result<(), RacpError> {
    loop {
        if parent()
            .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))?
            .is_some()
            && tree_empty().map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))?
        {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("CLEANUP_UNKNOWN"));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}
pub(crate) fn run(
    action: &str,
    cwd: &Path,
    input: Value,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<Value, RacpError> {
    if ![
        "image-preview-worker",
        "trash-worker",
        "diagnostic-worker",
        "hardware-discovery-worker",
    ]
    .contains(&action)
    {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    if action == "hardware-discovery-worker" && !cfg!(windows) {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    if cancel.is_cancelled() {
        return Err(RacpError::new("CANCELLED"));
    }
    if Instant::now() >= deadline {
        return Err(RacpError::new("TIMEOUT"));
    }
    let stdout_limit = if action == "hardware-discovery-worker" {
        4 * 1024 * 1024
    } else {
        16384
    };
    let bytes = serde_json::to_vec(&input)?;
    let input_limit = if action == "hardware-discovery-worker" {
        128
    } else {
        16384
    };
    if bytes.len() > input_limit {
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
        for (pipe, data, limit) in [
            (&mut child.stdout, &mut stdout, stdout_limit),
            (&mut child.stderr, &mut stderr, 16384),
        ] {
            for _ in 0..4 {
                match pipe.read_available(&mut buffer)? {
                    Some(n) if n > 0 => {
                        if data.len() + n > limit {
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
                    if stdout.len() + n > stdout_limit {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    stdout.extend_from_slice(&buffer[..n]);
                }
            }
            racp_contract::local_ipc::reject_duplicate_keys(&stdout)?;
            let result: Value = serde_json::from_slice(&stdout)?;
            if cancel.is_cancelled() {
                return Err(RacpError::new("CANCELLED"));
            }
            if Instant::now() >= deadline {
                return Err(RacpError::new("TIMEOUT"));
            }
            if code != 0 || result["ok"] != true {
                return Err(RacpError::new("EXECUTION_UNKNOWN"));
            }
            return Ok(result["result"].clone());
        }
        std::thread::sleep(Duration::from_millis(5));
    })();
    child.kill_tree()?;
    let cleanup = Instant::now() + Duration::from_secs(5);
    // A single mutable child borrow supplies both observations.
    let child = std::cell::RefCell::new(&mut child);
    cleanup_child(
        cleanup,
        || child.borrow_mut().poll(),
        || child.borrow().tree_empty(),
    )?;
    result
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn worker_cleanup_retains_unknown_until_parent_signal_even_when_job_is_empty() {
        let result = cleanup_child(Instant::now(), || Ok(None), || Ok(true));
        assert_eq!(result.unwrap_err().code.0, "CLEANUP_UNKNOWN");
    }
    #[test]
    fn worker_cleanup_requires_confirmed_entire_tree_and_propagates_query_errors() {
        assert!(cleanup_child(Instant::now(), || Ok(Some(0)), || Ok(false)).is_err());
        assert!(cleanup_child(
            Instant::now(),
            || Err(RacpError::new("EXECUTION_FAILED")),
            || Ok(true)
        )
        .is_err());
        cleanup_child(Instant::now(), || Ok(Some(0)), || Ok(true)).unwrap();
    }
}
