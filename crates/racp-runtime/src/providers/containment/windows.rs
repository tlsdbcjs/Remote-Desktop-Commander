use super::{CommandSpec, Pipe};
use racp_contract::RacpError;
use std::{
    fs::File,
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::{Path, PathBuf},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::SECURITY_ATTRIBUTES,
    Storage::FileSystem::*,
    System::{JobObjects::*, Pipes::CreatePipe, Threading::*},
};
fn wide(value: &std::ffi::OsStr) -> Vec<u16> {
    value.encode_wide().chain(Some(0)).collect()
}
fn own(raw: HANDLE) -> Result<OwnedHandle, RacpError> {
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        Err(RacpError::new("EXECUTION_FAILED"))
    } else {
        Ok(unsafe { OwnedHandle::from_raw_handle(raw) })
    }
}
fn pipe() -> Result<(File, OwnedHandle), RacpError> {
    let mut read = std::ptr::null_mut();
    let mut write = read;
    let sa = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: 1,
    };
    if unsafe { CreatePipe(&mut read, &mut write, &sa, 0) } == 0 {
        return Err(RacpError::new("EXECUTION_FAILED"));
    }
    let read = own(read)?;
    let write = own(write)?;
    if unsafe { SetHandleInformation(read.as_raw_handle(), HANDLE_FLAG_INHERIT, 0) } == 0 {
        return Err(RacpError::new("EXECUTION_FAILED"));
    }
    Ok((File::from(read), write))
}
fn quote(arg: &str) -> String {
    let mut quoted = String::from("\"");
    let mut slash = 0;
    for c in arg.chars() {
        if c == '\\' {
            slash += 1;
        } else {
            if c == '"' {
                quoted.extend(std::iter::repeat_n('\\', slash * 2 + 1));
            } else {
                quoted.extend(std::iter::repeat_n('\\', slash));
            }
            slash = 0;
            quoted.push(c);
        }
    }
    quoted.extend(std::iter::repeat_n('\\', slash * 2));
    quoted.push('"');
    quoted
}
fn executable(spec: &CommandSpec) -> Result<PathBuf, RacpError> {
    let path = Path::new(&spec.argv[0]);
    if path.is_absolute() {
        return Ok(path.into());
    }
    if path.components().count() != 1 {
        return Ok(spec.cwd.path.join(path));
    }
    let search = spec
        .environment
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case("PATH"))
        .map(|(_, v)| v.as_str())
        .unwrap_or("");
    for dir in std::env::split_paths(search) {
        if !dir.is_absolute() {
            continue;
        }
        let mut candidate = dir.join(path);
        if candidate.extension().is_none() {
            candidate.set_extension("exe");
        }
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err(RacpError::new("EXECUTION_FAILED"))
}
struct Attributes {
    storage: Vec<usize>,
    initialized: bool,
}
impl Attributes {
    fn new(handles: &[HANDLE]) -> Result<Self, RacpError> {
        let mut bytes = 0;
        unsafe { InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut bytes) };
        if bytes == 0 || bytes > 65536 {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        let mut this = Self {
            storage: vec![0; bytes.div_ceil(std::mem::size_of::<usize>())],
            initialized: false,
        };
        if unsafe { InitializeProcThreadAttributeList(this.raw(), 1, 0, &mut bytes) } == 0 {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        this.initialized = true;
        if unsafe {
            UpdateProcThreadAttribute(
                this.raw(),
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                handles.as_ptr().cast(),
                std::mem::size_of_val(handles),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(RacpError::new("EXECUTION_FAILED"));
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
pub struct OwnedProcess {
    process: OwnedHandle,
    job: OwnedHandle,
    pid: u32,
    pub stdout: Pipe,
    pub stderr: Pipe,
}
impl OwnedProcess {
    pub fn spawn(spec: CommandSpec) -> Result<Self, RacpError> {
        let program = executable(&spec)?;
        if program
            .extension()
            .is_none_or(|e| !e.to_string_lossy().eq_ignore_ascii_case("exe"))
        {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let program = wide(program.as_os_str());
        let cwd = wide(spec.cwd.path.as_os_str());
        let mut command = wide(std::ffi::OsStr::new(
            &spec
                .argv
                .iter()
                .map(|a| quote(a))
                .collect::<Vec<_>>()
                .join(" "),
        ));
        if command.len() > 32768 {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let mut environment = Vec::<u16>::new();
        let mut vars: Vec<_> = spec.environment.iter().collect();
        vars.sort_by_key(|(k, _)| k.to_ascii_uppercase());
        for (k, v) in vars {
            environment.extend(format!("{k}={v}").encode_utf16());
            environment.push(0);
        }
        environment.push(0);
        if environment.len() == 1 {
            environment.push(0);
        }
        let (stdout, out_write) = pipe()?;
        let (stderr, err_write) = pipe()?;
        let sa = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: std::ptr::null_mut(),
            bInheritHandle: 1,
        };
        let null = wide(std::ffi::OsStr::new("NUL"));
        let input = own(unsafe {
            CreateFileW(
                null.as_ptr(),
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                &sa,
                OPEN_EXISTING,
                0,
                std::ptr::null_mut(),
            )
        })?;
        let handles = [
            input.as_raw_handle(),
            out_write.as_raw_handle(),
            err_write.as_raw_handle(),
        ];
        let mut attributes = Attributes::new(&handles)?;
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = handles[0];
        startup.StartupInfo.hStdOutput = handles[1];
        startup.StartupInfo.hStdError = handles[2];
        startup.lpAttributeList = attributes.raw();
        let job = own(unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) })?;
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of_val(&limits) as u32,
            )
        } == 0
        {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        let mut info = PROCESS_INFORMATION::default();
        if unsafe {
            CreateProcessW(
                program.as_ptr(),
                command.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                CREATE_SUSPENDED
                    | CREATE_NO_WINDOW
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT,
                environment.as_ptr().cast(),
                cwd.as_ptr(),
                &startup.StartupInfo,
                &mut info,
            )
        } == 0
        {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        let process = own(info.hProcess)?;
        let thread = own(info.hThread)?;
        if unsafe { AssignProcessToJobObject(job.as_raw_handle(), process.as_raw_handle()) } == 0
            || unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX
        {
            unsafe {
                TerminateProcess(process.as_raw_handle(), 1);
                WaitForSingleObject(process.as_raw_handle(), 5000)
            };
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        Ok(Self {
            process,
            job,
            pid: info.dwProcessId,
            stdout: Pipe {
                file: stdout,
                eof: false,
            },
            stderr: Pipe {
                file: stderr,
                eof: false,
            },
        })
    }
    pub fn pid(&self) -> u32 {
        self.pid
    }
    pub fn tree_empty(&self) -> Result<bool, RacpError> {
        let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        if unsafe {
            QueryInformationJobObject(
                self.job.as_raw_handle(),
                JobObjectBasicAccountingInformation,
                (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                std::mem::size_of_val(&info) as u32,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        Ok(info.ActiveProcesses == 0)
    }
    pub fn poll(&mut self) -> Result<Option<i64>, RacpError> {
        match unsafe { WaitForSingleObject(self.process.as_raw_handle(), 0) } {
            WAIT_TIMEOUT => Ok(None),
            WAIT_OBJECT_0 => {
                let mut code = 0;
                if unsafe { GetExitCodeProcess(self.process.as_raw_handle(), &mut code) } == 0 {
                    return Err(RacpError::new("EXECUTION_FAILED"));
                }
                Ok(Some(code as i64))
            }
            _ => Err(RacpError::new("EXECUTION_FAILED")),
        }
    }
    pub fn kill_tree(&mut self) -> Result<(), RacpError> {
        if unsafe { TerminateJobObject(self.job.as_raw_handle(), 1) } == 0 {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        Ok(())
    }
}
impl Drop for OwnedProcess {
    fn drop(&mut self) {
        let _ = self.kill_tree();
        unsafe { WaitForSingleObject(self.process.as_raw_handle(), 5000) };
    }
}
