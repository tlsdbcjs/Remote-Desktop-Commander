//! Read-only, pinned process evidence for restart cleanup. Never signals a PID.
use racp_contract::RacpError;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use windows_sys::Win32::{Foundation::*, System::Threading::*};

pub(super) struct ProcessWitness(OwnedHandle);
fn absent_process(error: u32) -> Result<(), RacpError> {
    match error {
        ERROR_INVALID_PARAMETER => Ok(()),
        _ => Err(RacpError::new("CLEANUP_FAILED")),
    }
}
impl ProcessWitness {
    pub(super) fn open(pid: u32, expected: f64) -> Result<Option<Self>, RacpError> {
        if pid == 0 || pid > i32::MAX as u32 || !expected.is_finite() || expected <= 0.0 {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                pid,
            )
        };
        if raw.is_null() {
            absent_process(unsafe { GetLastError() })?;
            return Ok(None);
        }
        let owned = unsafe { OwnedHandle::from_raw_handle(raw) };
        let mut creation = FILETIME::default();
        let mut exit = creation;
        let mut kernel = creation;
        let mut user = creation;
        if unsafe {
            GetProcessTimes(
                owned.as_raw_handle(),
                &mut creation,
                &mut exit,
                &mut kernel,
                &mut user,
            )
        } == 0
        {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        let ticks = ((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64;
        let birth = (ticks / 10_000_000) as f64 + (ticks % 10_000_000) as f64 / 10_000_000.0
            - 11_644_473_600.0;
        if (birth - expected).abs() > 0.000001 {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        let witness = Self(owned);
        if !witness.exited()? {
            // A live process must belong to this actor. A concurrent exit can
            // retire the token; only the same pinned handle's signal admits it.
            let actor = crate::identity::token_sid(unsafe { GetCurrentProcess() })?;
            match crate::identity::token_sid(witness.0.as_raw_handle()) {
                Ok(sid) if sid == actor => {}
                Err(_) if witness.exited()? => {}
                _ => return Err(RacpError::new("CLEANUP_FAILED")),
            }
        }
        Ok(Some(witness))
    }
    pub(super) fn exited(&self) -> Result<bool, RacpError> {
        match unsafe { WaitForSingleObject(self.0.as_raw_handle(), 0) } {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            _ => Err(RacpError::new("CLEANUP_FAILED")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "owned subprocess fixture only"]
    fn exit_fixture() {
        let mut input = String::new();
        std::io::stdin().read_line(&mut input).unwrap();
    }
    #[test]
    fn recovery_witness_accepts_exact_birth_after_exit_and_retains_mismatched_identity() {
        use std::io::Write;
        let fixture = concat!(
            "providers::browser::recovery::windows::tests::",
            "exit_fixture"
        );
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--ignored", "--exact", fixture])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let birth = crate::identity::process_created(child.id()).unwrap();
        let witness = ProcessWitness::open(child.id(), birth).unwrap().unwrap();
        assert!(!witness.exited().unwrap());
        assert!(ProcessWitness::open(child.id(), birth + 1.0).is_err());
        child.stdin.take().unwrap().write_all(b"exit\n").unwrap();
        child.wait().unwrap();
        assert!(witness.exited().unwrap());
        let after_exit = ProcessWitness::open(child.id(), birth).unwrap().unwrap();
        assert!(after_exit.exited().unwrap());
        assert!(ProcessWitness::open(child.id(), birth + 1.0).is_err());
    }
    #[test]
    fn recovery_witness_does_not_accept_invalid_pid_or_unbounded_birth() {
        assert!(ProcessWitness::open(0, 1.0).is_err());
        assert!(ProcessWitness::open(u32::MAX, 1.0).is_err());
        assert!(ProcessWitness::open(std::process::id(), f64::NAN).is_err());
        assert!(ProcessWitness::open(std::process::id(), f64::INFINITY).is_err());
        assert!(ProcessWitness::open(std::process::id(), 0.0).is_err());
        assert!(ProcessWitness::open(i32::MAX as u32, 1.0)
            .unwrap()
            .is_none());
        assert!(absent_process(ERROR_INVALID_PARAMETER).is_ok());
        for error in [ERROR_ACCESS_DENIED, ERROR_INVALID_HANDLE, 0, u32::MAX] {
            assert!(absent_process(error).is_err());
        }
    }
}
