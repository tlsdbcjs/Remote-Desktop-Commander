//! A background Agent may outlive its controller, but never inherits controller pipes.
use racp_contract::RacpError;
use std::path::Path;
#[cfg(unix)]
pub struct BackgroundChild(std::process::Child);
#[cfg(unix)]
impl BackgroundChild {
    pub fn id(&self) -> u32 {
        self.0.id()
    }
    pub fn exited(&mut self) -> Result<bool, RacpError> {
        Ok(self.0.try_wait()?.is_some())
    }
}
#[cfg(unix)]
pub fn spawn(executable: &Path, state: &Path) -> Result<BackgroundChild, RacpError> {
    use std::os::unix::process::CommandExt;
    let mut command = std::process::Command::new(executable);
    command
        .args(["serve", "--state-dir"])
        .arg(state)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() < 0 {
                Err(std::io::Error::last_os_error())
            } else {
                Ok(())
            }
        });
    }
    command
        .spawn()
        .map(BackgroundChild)
        .map_err(|_| RacpError::new("RUNTIME_UNAVAILABLE"))
}
#[cfg(windows)]
mod windows {
    use super::*;
    use std::os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    };
    use windows_sys::Win32::{
        Foundation::*, Security::SECURITY_ATTRIBUTES, Storage::FileSystem::*, System::Threading::*,
    };
    pub struct BackgroundChild {
        handle: OwnedHandle,
        pid: u32,
    }
    impl BackgroundChild {
        pub fn id(&self) -> u32 {
            self.pid
        }
        pub fn exited(&mut self) -> Result<bool, RacpError> {
            match unsafe { WaitForSingleObject(self.handle.as_raw_handle(), 0) } {
                WAIT_TIMEOUT => Ok(false),
                WAIT_OBJECT_0 => Ok(true),
                _ => Err(RacpError::new("RUNTIME_UNAVAILABLE")),
            }
        }
    }
    fn wide(value: &std::ffi::OsStr) -> Vec<u16> {
        value.encode_wide().chain(Some(0)).collect()
    }
    fn quote(value: &std::ffi::OsStr) -> String {
        let mut out = String::from("\"");
        let mut slashes = 0;
        for c in value.to_string_lossy().chars() {
            if c == '\\' {
                slashes += 1;
            } else {
                out.extend(std::iter::repeat_n(
                    '\\',
                    if c == '"' { slashes * 2 + 1 } else { slashes },
                ));
                out.push(c);
                slashes = 0;
            }
        }
        out.extend(std::iter::repeat_n('\\', slashes * 2));
        out.push('"');
        out
    }
    struct Attributes {
        storage: Vec<usize>,
        initialized: bool,
    }
    impl Attributes {
        fn new(handle: &HANDLE) -> Result<Self, RacpError> {
            let mut bytes = 0;
            unsafe { InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut bytes) };
            if bytes == 0 || bytes > 65536 {
                return Err(RacpError::new("RUNTIME_UNAVAILABLE"));
            }
            let mut this = Self {
                storage: vec![0; bytes.div_ceil(std::mem::size_of::<usize>())],
                initialized: false,
            };
            if unsafe { InitializeProcThreadAttributeList(this.raw(), 1, 0, &mut bytes) } == 0 {
                return Err(RacpError::new("RUNTIME_UNAVAILABLE"));
            }
            this.initialized = true;
            if unsafe {
                UpdateProcThreadAttribute(
                    this.raw(),
                    0,
                    PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                    (handle as *const HANDLE).cast(),
                    std::mem::size_of::<HANDLE>(),
                    std::ptr::null_mut(),
                    std::ptr::null(),
                )
            } == 0
            {
                return Err(RacpError::new("RUNTIME_UNAVAILABLE"));
            }
            Ok(this)
        }
        fn raw(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
            self.storage.as_mut_ptr().cast()
        }
    }
    impl Drop for Attributes {
        fn drop(&mut self) {
            if self.initialized {
                unsafe { DeleteProcThreadAttributeList(self.raw()) };
            }
        }
    }
    pub fn spawn(executable: &Path, state: &Path) -> Result<BackgroundChild, RacpError> {
        let program = wide(executable.as_os_str());
        let mut command = wide(std::ffi::OsStr::new(&format!(
            "{} serve --state-dir {}",
            quote(executable.as_os_str()),
            quote(state.as_os_str())
        )));
        if command.len() > 32768 {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let sa = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: std::ptr::null_mut(),
            bInheritHandle: 1,
        };
        let name = wide(std::ffi::OsStr::new("NUL"));
        let raw = unsafe {
            CreateFileW(
                name.as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                &sa,
                OPEN_EXISTING,
                0,
                std::ptr::null_mut(),
            )
        };
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            return Err(RacpError::new("RUNTIME_UNAVAILABLE"));
        }
        let null = unsafe { OwnedHandle::from_raw_handle(raw) };
        let handle = null.as_raw_handle();
        let mut attributes = Attributes::new(&handle)?;
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = handle;
        startup.StartupInfo.hStdOutput = handle;
        startup.StartupInfo.hStdError = handle;
        startup.lpAttributeList = attributes.raw();
        let mut info = PROCESS_INFORMATION::default();
        if unsafe {
            CreateProcessW(
                program.as_ptr(),
                command.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                DETACHED_PROCESS
                    | CREATE_NEW_PROCESS_GROUP
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT,
                std::ptr::null(),
                std::ptr::null(),
                &startup.StartupInfo,
                &mut info,
            )
        } == 0
        {
            return Err(RacpError::new("RUNTIME_UNAVAILABLE"));
        }
        let process = unsafe { OwnedHandle::from_raw_handle(info.hProcess) };
        let thread = unsafe { OwnedHandle::from_raw_handle(info.hThread) };
        drop(thread);
        Ok(BackgroundChild {
            handle: process,
            pid: info.dwProcessId,
        })
    }
}
#[cfg(windows)]
pub use windows::{spawn, BackgroundChild};
