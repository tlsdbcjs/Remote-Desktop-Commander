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
    System::{Console::*, JobObjects::*, Pipes::CreatePipe, Threading::*},
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
        if !handles.is_empty()
            && unsafe {
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
    job_name: String,
    pid: u32,
    console: Option<HPCON>,
    pub stdout: Pipe,
    pub stderr: Pipe,
}
impl OwnedProcess {
    pub fn spawn_pty(spec: CommandSpec, cols: u16, rows: u16) -> Result<(Self, File), RacpError> {
        let (input_read, input_write) = pipe()?;
        let (stdout, out_write) = pipe()?;
        let mut console = 0;
        if unsafe {
            CreatePseudoConsole(
                COORD {
                    X: cols as i16,
                    Y: rows as i16,
                },
                input_read.as_raw_handle(),
                out_write.as_raw_handle(),
                0,
                &mut console,
            )
        } < 0
        {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let result = (|| {
            let mut attributes = Attributes::new(&[])?;
            if unsafe {
                UpdateProcThreadAttribute(
                    attributes.raw(),
                    0,
                    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE as usize,
                    console as *const core::ffi::c_void,
                    std::mem::size_of::<HPCON>(),
                    std::ptr::null_mut(),
                    std::ptr::null(),
                )
            } == 0
            {
                return Err(RacpError::new("EXECUTION_FAILED"));
            }
            let mut startup = STARTUPINFOEXW::default();
            startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
            startup.lpAttributeList = attributes.raw();
            let null = wide(std::ffi::OsStr::new("NUL"));
            let stderr = File::from(own(unsafe {
                CreateFileW(
                    null.as_ptr(),
                    GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    std::ptr::null(),
                    OPEN_EXISTING,
                    0,
                    std::ptr::null_mut(),
                )
            })?);
            let mut process = Self::launch(spec, stdout, stderr, &startup, false, Some(console))?;
            process.stderr.eof = true;
            Ok((process, File::from(input_write)))
        })();
        drop(input_read);
        drop(out_write);
        if result.is_err() {
            unsafe { ClosePseudoConsole(console) };
        }
        result
    }
    pub fn resize(&self, cols: u16, rows: u16) -> Result<(), RacpError> {
        let console = self
            .console
            .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
        if unsafe {
            ResizePseudoConsole(
                console,
                COORD {
                    X: cols as i16,
                    Y: rows as i16,
                },
            )
        } < 0
        {
            return Err(RacpError::new("HANDLE_EXPIRED"));
        }
        Ok(())
    }
    pub fn finish_pty(&mut self, mut receive: impl FnMut(&[u8])) -> Result<Option<i64>, RacpError> {
        self.kill_tree()?;
        let (receiver, closing) = if let Some(console) = self.console.take() {
            let (tx, rx) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                unsafe { ClosePseudoConsole(console) };
                let _ = tx.send(());
            });
            (Some(rx), true)
        } else {
            (None, false)
        };
        let until = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut finished = !closing;
        let mut code = None;
        let mut block = [0u8; 65536];
        loop {
            for _ in 0..16 {
                match self.stdout.read_available(&mut block)? {
                    Some(n) if n > 0 => receive(&block[..n]),
                    _ => break,
                }
            }
            if let Some(exit) = self.poll()? {
                code = Some(exit);
            }
            if receiver.as_ref().is_some_and(|rx| rx.try_recv().is_ok()) {
                finished = true;
            }
            if finished && code.is_some() && self.tree_empty()? && self.stdout.eof() {
                return Ok(code);
            }
            if std::time::Instant::now() >= until {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }
    pub fn spawn(spec: CommandSpec) -> Result<Self, RacpError> {
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
        Self::spawn_with_input(spec, input)
    }
    pub fn spawn_stdio(spec: CommandSpec) -> Result<(Self, File), RacpError> {
        let (input, write) = pipe()?;
        if unsafe {
            SetHandleInformation(
                input.as_raw_handle(),
                HANDLE_FLAG_INHERIT,
                HANDLE_FLAG_INHERIT,
            )
        } == 0
            || unsafe { SetHandleInformation(write.as_raw_handle(), HANDLE_FLAG_INHERIT, 0) } == 0
        {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        Ok((
            Self::spawn_with_input(spec, input.into())?,
            File::from(write),
        ))
    }
    fn spawn_with_input(spec: CommandSpec, input: OwnedHandle) -> Result<Self, RacpError> {
        let (stdout, out_write) = pipe()?;
        let (stderr, err_write) = pipe()?;
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
        Self::launch(spec, stdout, stderr, &startup, true, None)
    }
    fn launch(
        spec: CommandSpec,
        stdout: File,
        stderr: File,
        startup: &STARTUPINFOEXW,
        inherit: bool,
        console: Option<HPCON>,
    ) -> Result<Self, RacpError> {
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
        let job_name = format!("Local\\RACP_{}", racp_contract::new_id("job"));
        let name = wide(std::ffi::OsStr::new(&job_name));
        let job = own(unsafe { CreateJobObjectW(std::ptr::null(), name.as_ptr()) })?;
        if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
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
                i32::from(inherit),
                CREATE_SUSPENDED
                    | if console.is_none() {
                        CREATE_NO_WINDOW
                    } else {
                        0
                    }
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
            job_name,
            pid: info.dwProcessId,
            console,
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
    pub fn job_name(&self) -> Option<&str> {
        Some(&self.job_name)
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
    pub fn limit_memory(&self, bytes: u64) -> Result<(), RacpError> {
        if !(64 * 1024 * 1024..=8 * 1024 * 1024 * 1024).contains(&bytes) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY;
        limits.JobMemoryLimit = bytes as usize;
        if unsafe {
            SetInformationJobObject(
                self.job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of_val(&limits) as u32,
            )
        } == 0
        {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        Ok(())
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
        if self.console.is_some() {
            let _ = self.finish_pty(|_| {});
        }
        let _ = self.kill_tree();
        unsafe { WaitForSingleObject(self.process.as_raw_handle(), 5000) };
    }
}
