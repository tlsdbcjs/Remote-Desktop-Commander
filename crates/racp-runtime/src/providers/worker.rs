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
    // Hold protection until explicit termination has been confirmed, including
    // setup errors. A fallible setup step must never bypass finish_owned.
    let mut protected = None;
    let result = (|| {
        #[cfg(windows)]
        child.limit_memory(256 * 1024 * 1024)?;
        protected = Some(crate::identity::ProtectedProcess::register(child.pid())?);
        stdin.write_all(&bytes)?;
        stdin.write_all(b"\n")?;
        drop(stdin);
        let (code, stdout) = capture_child(&mut child, stdout_limit, deadline, cancel)?;
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
        Ok(result["result"].clone())
    })();
    finish_owned(&mut child, result)
}
fn finish_owned<T>(child: &mut OwnedProcess, result: Result<T, RacpError>) -> Result<T, RacpError> {
    child
        .kill_tree()
        .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))?;
    let cleanup = Instant::now() + Duration::from_secs(5);
    let child = std::cell::RefCell::new(child);
    cleanup_child(
        cleanup,
        || child.borrow_mut().poll(),
        || child.borrow().tree_empty(),
    )?;
    result
}
fn capture_child(
    child: &mut OwnedProcess,
    stdout_limit: usize,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<(i64, Vec<u8>), RacpError> {
    let mut stdout = vec![];
    let mut stderr = vec![];
    let mut buffer = [0u8; 4096];
    loop {
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
        // Parent exit does not imply empty pipes. Drain both streams to EOF,
        // with the same limits/deadline, including late stderr overflow.
        if let Some(code) = child.poll()? {
            if child.stdout.eof() && child.stderr.eof() {
                return Ok((code, stdout));
            }
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "owned subprocess fixture; invoked by worker capture tests only"]
    fn frame_fixture() {
        if std::env::var("RACP_WORKER_TEST_FRAME").as_deref() == Ok("overflow") {
            std::io::stderr().write_all(&vec![b'e'; 20_000]).unwrap();
        } else {
            std::io::stdout().write_all(&vec![b'x'; 131_072]).unwrap();
        }
        std::process::exit(0);
    }
    fn fixture(root: &Path, overflow: bool) -> OwnedProcess {
        let (child, stdin) = OwnedProcess::spawn_stdio(CommandSpec {
            argv: vec![
                std::env::current_exe()
                    .unwrap()
                    .to_string_lossy()
                    .into_owned(),
                "--ignored".into(),
                "--exact".into(),
                "providers::worker::tests::frame_fixture".into(),
                "--nocapture".into(),
            ],
            environment: BTreeMap::from([(
                "RACP_WORKER_TEST_FRAME".into(),
                if overflow { "overflow" } else { "large" }.into(),
            )]),
            cwd: racp_core::Workspaces::new(root, &[])
                .unwrap()
                .directory("default", root)
                .unwrap(),
        })
        .unwrap();
        drop(stdin);
        child
    }
    #[test]
    fn worker_capture_drains_large_frame_to_eof_and_proves_cleanup() {
        let root = tempfile::tempdir().unwrap();
        let mut child = fixture(root.path(), false);
        let result = capture_child(
            &mut child,
            4 * 1024 * 1024,
            Instant::now() + Duration::from_secs(10),
            &CancellationToken::new(),
        );
        let (code, bytes) = finish_owned(&mut child, result).unwrap();
        assert_eq!(code, 0);
        assert_eq!(bytes.iter().filter(|b| **b == b'x').count(), 131_072);
        assert!(child.stdout.eof() && child.stderr.eof());
        assert!(child.tree_empty().unwrap());
        assert!(child.poll().unwrap().is_some());
    }
    #[test]
    fn worker_capture_rejects_stderr_overflow_and_cleans_early_setup_error() {
        let root = tempfile::tempdir().unwrap();
        let mut child = fixture(root.path(), true);
        let result = capture_child(
            &mut child,
            4 * 1024 * 1024,
            Instant::now() + Duration::from_secs(10),
            &CancellationToken::new(),
        );
        assert_eq!(
            finish_owned(&mut child, result).unwrap_err().code.0,
            "RESOURCE_EXHAUSTED"
        );
        assert!(child.tree_empty().unwrap());
        assert!(child.poll().unwrap().is_some());
        let mut child = fixture(root.path(), false);
        let result: Result<(), RacpError> = Err(RacpError::new("PERMISSION_DENIED"));
        assert_eq!(
            finish_owned(&mut child, result).unwrap_err().code.0,
            "PERMISSION_DENIED"
        );
        assert!(child.tree_empty().unwrap());
        assert!(child.poll().unwrap().is_some());
    }
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
