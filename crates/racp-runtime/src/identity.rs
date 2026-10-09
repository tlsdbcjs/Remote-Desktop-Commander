use racp_contract::RacpError;
/// Signal only the process whose native birth identity was authorized.
pub fn signal_process(pid: u32, expected: f64, force: bool) -> Result<(), RacpError> {
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
        let raw = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) };
        if raw < 0 {
            return Err(RacpError::new("PROCESS_NOT_FOUND"));
        }
        let fd = unsafe { OwnedFd::from_raw_fd(raw as i32) };
        if (process_created(pid)? - expected).abs() > 0.000001 {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let mut poll = libc::pollfd {
            fd: fd.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        if unsafe { libc::poll(&mut poll, 1, 0) } != 0 {
            return Err(RacpError::new("PROCESS_NOT_FOUND"));
        }
        let signal = if force { libc::SIGKILL } else { libc::SIGTERM };
        if unsafe {
            libc::syscall(
                libc::SYS_pidfd_send_signal,
                fd.as_raw_fd(),
                signal,
                std::ptr::null::<libc::siginfo_t>(),
                0,
            )
        } < 0
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(())
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::{Foundation::*, System::Threading::*, UI::WindowsAndMessaging::*};
        let handle = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION
                    | PROCESS_SYNCHRONIZE
                    | if force { PROCESS_TERMINATE } else { 0 },
                0,
                pid,
            )
        };
        if handle.is_null() {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(handle) };
        let raw = handle.as_raw_handle();
        if token_sid(raw)? != token_sid(unsafe { GetCurrentProcess() })? {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let mut creation = FILETIME::default();
        let mut exit = creation;
        let mut kernel = creation;
        let mut user = creation;
        if unsafe { GetProcessTimes(raw, &mut creation, &mut exit, &mut kernel, &mut user) } == 0
            || unsafe { WaitForSingleObject(raw, 0) } != WAIT_TIMEOUT
        {
            return Err(RacpError::new("PROCESS_NOT_FOUND"));
        }
        let ticks = ((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64;
        let birth = (ticks / 10_000_000) as f64 + (ticks % 10_000_000) as f64 / 10_000_000.0
            - 11_644_473_600.0;
        if (birth - expected).abs() > 0.000001 {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        if force {
            if unsafe { TerminateProcess(raw, 1) } == 0 {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            return Ok(());
        }
        struct Close {
            pid: u32,
            sent: bool,
        }
        unsafe extern "system" fn close(window: HWND, context: LPARAM) -> i32 {
            let state = &mut *(context as *mut Close);
            let mut pid = 0;
            GetWindowThreadProcessId(window, &mut pid);
            if pid == state.pid && PostMessageW(window, WM_CLOSE, 0, 0) != 0 {
                state.sent = true;
            }
            1
        }
        let mut state = Close { pid, sent: false };
        unsafe { EnumWindows(Some(close), (&mut state as *mut Close) as isize) };
        if !state.sent {
            return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
        }
        Ok(())
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    {
        let _ = (pid, expected, force);
        Err(RacpError::new("OPERATION_NOT_SUPPORTED"))
    }
}
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
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::System::Threading::GetCurrentProcess;
        use windows_sys::Win32::{
            Foundation::{FILETIME, WAIT_TIMEOUT},
            System::Threading::{
                GetProcessTimes, OpenProcess, WaitForSingleObject,
                PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
            },
        };
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                pid,
            )
        };
        if raw.is_null() {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        let owned = unsafe { OwnedHandle::from_raw_handle(raw) };
        let handle = owned.as_raw_handle();
        if token_sid(handle)? != token_sid(unsafe { GetCurrentProcess() })? {
            return Err(RacpError::new("PERMISSION_DENIED"));
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

#[cfg(windows)]
fn token_sid(process: windows_sys::Win32::Foundation::HANDLE) -> Result<Vec<u8>, RacpError> {
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::{
        Security::{
            GetLengthSid, GetTokenInformation, IsValidSid, TokenUser, TOKEN_QUERY, TOKEN_USER,
        },
        System::Threading::OpenProcessToken,
    };
    let denied = || RacpError::new("PERMISSION_DENIED");
    let mut raw = std::ptr::null_mut();
    if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut raw) } == 0 {
        return Err(denied());
    }
    let token = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut size = 0u32;
    unsafe {
        GetTokenInformation(
            token.as_raw_handle(),
            TokenUser,
            std::ptr::null_mut(),
            0,
            &mut size,
        );
    }
    if size < (std::mem::size_of::<TOKEN_USER>() as u32) || size > 65536 {
        return Err(denied());
    }
    // Explicit word alignment is required before reading TOKEN_USER from the buffer.
    let mut buffer = vec![0usize; (size as usize).div_ceil(std::mem::size_of::<usize>())];
    if unsafe {
        GetTokenInformation(
            token.as_raw_handle(),
            TokenUser,
            buffer.as_mut_ptr().cast(),
            size,
            &mut size,
        )
    } == 0
    {
        return Err(denied());
    }
    let user = unsafe { &*buffer.as_ptr().cast::<TOKEN_USER>() };
    let sid = user.User.Sid;
    let start = buffer.as_ptr() as usize;
    let end = start + size as usize;
    let address = sid as usize;
    if address < start || address + 8 > end || unsafe { IsValidSid(sid) } == 0 {
        return Err(denied());
    }
    let length = unsafe { GetLengthSid(sid) } as usize;
    if length > 128 || address + length > end {
        return Err(denied());
    }
    Ok(unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), length) }.to_vec())
}

/// Native provider processes and their descendants cannot be controlled as user targets.
static PROTECTED: std::sync::LazyLock<std::sync::Mutex<std::collections::BTreeMap<u32, f64>>> =
    std::sync::LazyLock::new(Default::default);
pub struct ProtectedProcess(u32);
impl ProtectedProcess {
    pub fn register(pid: u32) -> Result<Self, RacpError> {
        let birth = process_created(pid)?;
        PROTECTED
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .insert(pid, birth);
        Ok(Self(pid))
    }
}
impl Drop for ProtectedProcess {
    fn drop(&mut self) {
        if let Ok(mut protected) = PROTECTED.lock() {
            protected.remove(&self.0);
        }
    }
}
pub fn provider_protected(system: &sysinfo::System, pid: u32) -> bool {
    let Ok(roots) = PROTECTED.lock() else {
        return true;
    };
    let mut target = Some(sysinfo::Pid::from_u32(pid));
    let mut seen = std::collections::BTreeSet::new();
    for _ in 0..128 {
        let Some(current) = target else {
            break;
        };
        if !seen.insert(current) {
            return true;
        }
        if let Some(expected) = roots.get(&current.as_u32()) {
            if process_created(current.as_u32())
                .is_ok_and(|observed| (observed - expected).abs() <= 0.000001)
            {
                return true;
            }
        }
        target = system.process(current).and_then(|p| p.parent());
    }
    false
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
