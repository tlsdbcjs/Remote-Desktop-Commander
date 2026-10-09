use racp_contract::RacpError;
pub fn process_created(pid: u32) -> Result<f64, RacpError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let directory = format!("/proc/{pid}");
        let metadata = std::fs::metadata(&directory)?;
        if metadata.uid() != unsafe { libc::geteuid() } {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let stat = std::fs::read_to_string(format!("{directory}/stat"))?;
        let rest = stat
            .rsplit_once(')')
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?
            .1;
        let fields: Vec<_> = rest.split_whitespace().collect();
        if fields.first() == Some(&"Z") {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        let ticks: f64 = fields
            .get(19)
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?
            .parse()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let boot: f64 = std::fs::read_to_string("/proc/stat")?
            .lines()
            .find_map(|l| l.strip_prefix("btime "))
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?
            .parse()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let frequency = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
        if frequency <= 0 {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        Ok(ticks / frequency as f64 + boot)
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::{
            Foundation::{CloseHandle, FILETIME, WAIT_TIMEOUT},
            System::Threading::{
                GetProcessTimes, OpenProcess, WaitForSingleObject,
                PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
            },
        };
        let mut system = sysinfo::System::new();
        let ids = [
            sysinfo::Pid::from_u32(pid),
            sysinfo::Pid::from_u32(std::process::id()),
        ];
        system.refresh_processes_specifics(
            sysinfo::ProcessesToUpdate::Some(&ids),
            true,
            sysinfo::ProcessRefreshKind::everything(),
        );
        let a = system.process(ids[0]).and_then(|p| p.user_id());
        let b = system.process(ids[1]).and_then(|p| p.user_id());
        if a.is_none() || a != b {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let handle = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                pid,
            )
        };
        if handle.is_null() {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        let mut creation = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        let mut exit = creation;
        let mut kernel = creation;
        let mut user = creation;
        let ok =
            unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) };
        let running = unsafe { WaitForSingleObject(handle, 0) } == WAIT_TIMEOUT;
        unsafe {
            CloseHandle(handle);
        }
        if ok == 0 || !running {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        let ticks = ((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64;
        Ok(
            (ticks / 10_000_000) as f64 + (ticks % 10_000_000) as f64 / 10_000_000.0
                - 11_644_473_600.0,
        )
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn own_birth_identity_is_stable_and_dead_pid_is_rejected() {
        let own = std::process::id();
        let first = super::process_created(own).unwrap();
        assert!(first > 0.0);
        assert_eq!(super::process_created(own).unwrap(), first);
        assert!(super::process_created(u32::MAX).is_err());
    }
}
