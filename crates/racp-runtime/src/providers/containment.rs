//! Native containment is established before the child's first instruction.
use racp_contract::RacpError;
use racp_core::Directory;
use std::{collections::BTreeMap, fs::File, io::Read};
pub struct CommandSpec {
    pub argv: Vec<String>,
    pub environment: BTreeMap<String, String>,
    pub cwd: Directory,
}
pub fn environment(overrides: &serde_json::Value) -> Result<BTreeMap<String, String>, RacpError> {
    let allowed = [
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
    ];
    let protected = [
        "PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "COMSPEC",
        "SYSTEMROOT",
        "WINDIR",
    ];
    let mut env: BTreeMap<String, String> = std::env::vars()
        .filter(|(k, _)| allowed.contains(&k.to_ascii_uppercase().as_str()))
        .collect();
    if let Some(values) = overrides.as_object() {
        for (k, v) in values {
            let upper = k.to_ascii_uppercase();
            if protected.contains(&upper.as_str())
                || ["RACP_", "OPENAI_", "AWS_"]
                    .iter()
                    .any(|p| upper.starts_with(p))
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            if k.is_empty()
                || k.contains(['=', '\0'])
                || v.as_str().is_some_and(|s| s.contains('\0'))
            {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            #[cfg(windows)]
            env.retain(|old, _| old.to_ascii_uppercase() != upper);
            if let Some(value) = v.as_str() {
                env.insert(k.clone(), value.into());
            } else {
                env.remove(k);
            }
        }
    }
    Ok(env)
}
pub fn argv(payload: &serde_json::Value) -> Result<Vec<String>, RacpError> {
    if payload["mode"] == "argv" {
        let args: Vec<String> = payload["argv"]
            .as_array()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
            .iter()
            .map(|s| s.as_str().unwrap_or("").to_owned())
            .collect();
        if args.is_empty() || args[0].is_empty() || args.iter().any(|s| s.contains('\0')) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        #[cfg(windows)]
        if std::path::Path::new(&args[0]).extension().is_some_and(|e| {
            e.to_string_lossy().eq_ignore_ascii_case("bat")
                || e.to_string_lossy().eq_ignore_ascii_case("cmd")
        }) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        return Ok(args);
    }
    let shell = payload["shell"].as_str().unwrap_or("");
    #[cfg(unix)]
    let mut args = if shell == "bash" {
        vec!["/bin/bash".into(), "-c".into()]
    } else {
        return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
    };
    #[cfg(windows)]
    let mut args = {
        let system = std::env::var("SYSTEMROOT").unwrap_or_else(|_| "C:\\Windows".into());
        match shell {
            "cmd" => vec![
                format!("{system}\\System32\\cmd.exe"),
                "/d".into(),
                "/s".into(),
                "/c".into(),
            ],
            "powershell" => vec![
                format!("{system}\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"),
                "-NoProfile".into(),
                "-NonInteractive".into(),
                "-Command".into(),
            ],
            _ => return Err(RacpError::new("OPERATION_NOT_SUPPORTED")),
        }
    };
    args.push(
        payload["command"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
            .into(),
    );
    Ok(args)
}
pub struct Pipe {
    file: File,
    eof: bool,
}
impl Pipe {
    pub fn read_available(&mut self, buffer: &mut [u8]) -> std::io::Result<Option<usize>> {
        if self.eof {
            return Ok(Some(0));
        }
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use windows_sys::Win32::System::Pipes::PeekNamedPipe;
            let mut available = 0;
            if unsafe {
                PeekNamedPipe(
                    self.file.as_raw_handle(),
                    std::ptr::null_mut(),
                    0,
                    std::ptr::null_mut(),
                    &mut available,
                    std::ptr::null_mut(),
                )
            } == 0
            {
                let error = std::io::Error::last_os_error();
                if matches!(error.raw_os_error(), Some(109 | 232)) {
                    self.eof = true;
                    return Ok(Some(0));
                }
                return Err(error);
            }
            if available == 0 {
                return Ok(None);
            }
            let size = buffer.len().min(available as usize);
            let n = self.file.read(&mut buffer[..size])?;
            self.eof = n == 0;
            Ok(Some(n))
        }
        #[cfg(unix)]
        match self.file.read(buffer) {
            Ok(n) => {
                self.eof = n == 0;
                Ok(Some(n))
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
            Err(e) if e.raw_os_error() == Some(libc::EIO) => {
                self.eof = true;
                Ok(Some(0))
            }
            Err(e) => Err(e),
        }
    }
    pub fn eof(&self) -> bool {
        self.eof
    }
}
#[cfg(unix)]
pub struct OwnedProcess {
    child: std::process::Child,
    pid: u32,
    pub stdout: Pipe,
    pub stderr: Pipe,
}
#[cfg(unix)]
impl OwnedProcess {
    pub fn spawn_pty(spec: CommandSpec, cols: u16, rows: u16) -> Result<(Self, File), RacpError> {
        use std::os::{
            fd::{AsRawFd, FromRawFd},
            unix::process::CommandExt,
        };
        let (mut master, mut slave) = (-1, -1);
        let size = libc::winsize {
            ws_row: rows,
            ws_col: cols,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        if unsafe {
            libc::openpty(
                &mut master,
                &mut slave,
                std::ptr::null_mut(),
                std::ptr::null(),
                &size,
            )
        } < 0
        {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let master = unsafe { File::from_raw_fd(master) };
        let slave = unsafe { File::from_raw_fd(slave) };
        for fd in [master.as_raw_fd(), slave.as_raw_fd()] {
            if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
                return Err(RacpError::new("EXECUTION_FAILED"));
            }
        }
        let flags = unsafe { libc::fcntl(master.as_raw_fd(), libc::F_GETFL) };
        if flags < 0
            || unsafe { libc::fcntl(master.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) }
                < 0
        {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        let input = master.try_clone()?;
        let cwd = spec.cwd.handle().try_clone()?;
        let mut command = std::process::Command::new(&spec.argv[0]);
        command
            .args(&spec.argv[1..])
            .env_clear()
            .envs(spec.environment)
            .stdin(slave.try_clone()?)
            .stdout(slave.try_clone()?)
            .stderr(slave);
        unsafe {
            command.pre_exec(move || {
                if libc::setsid() < 0
                    || libc::fchdir(cwd.as_raw_fd()) < 0
                    || libc::ioctl(0, libc::TIOCSCTTY, 0) < 0
                {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = command
            .spawn()
            .map_err(|_| RacpError::new("PATH_NOT_FOUND"))?;
        let pid = child.id();
        Ok((
            Self {
                child,
                pid,
                stdout: Pipe {
                    file: master,
                    eof: false,
                },
                stderr: Pipe {
                    file: File::open("/dev/null")?,
                    eof: true,
                },
            },
            input,
        ))
    }
    pub fn resize(&self, cols: u16, rows: u16) -> Result<(), RacpError> {
        use std::os::fd::AsRawFd;
        let size = libc::winsize {
            ws_row: rows,
            ws_col: cols,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        if unsafe { libc::ioctl(self.stdout.file.as_raw_fd(), libc::TIOCSWINSZ, &size) } < 0 {
            return Err(RacpError::new("HANDLE_EXPIRED"));
        }
        Ok(())
    }
    pub fn finish_pty(&mut self, mut receive: impl FnMut(&[u8])) -> Result<Option<i64>, RacpError> {
        self.kill_tree()?;
        let until = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut code = None;
        let mut block = [0u8; 65536];
        loop {
            match self.stdout.read_available(&mut block)? {
                Some(n) if n > 0 => receive(&block[..n]),
                _ => {}
            }
            if let Some(exit) = self.poll()? {
                code = Some(exit);
            }
            if code.is_some() && self.stdout.eof() {
                return Ok(code);
            }
            if std::time::Instant::now() >= until {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }
    pub fn spawn(spec:CommandSpec)->Result<Self,RacpError>{Self::spawn_with_input(spec,false).map(|(child,_)|child)}
    pub fn spawn_stdio(spec:CommandSpec)->Result<(Self,File),RacpError>{let (child,input)=Self::spawn_with_input(spec,true)?;Ok((child,input.ok_or_else(||RacpError::new("EXECUTION_FAILED"))?))}
    fn spawn_with_input(spec: CommandSpec,pipe_input:bool) -> Result<(Self,Option<File>), RacpError> {
        use std::os::{
            fd::{AsRawFd, OwnedFd},
            unix::process::CommandExt,
        };
        let cwd = spec.cwd.handle().try_clone()?;
        let mut command = std::process::Command::new(&spec.argv[0]);
        command
            .args(&spec.argv[1..])
            .env_clear()
            .envs(spec.environment)
            .stdin(if pipe_input{std::process::Stdio::piped()}else{std::process::Stdio::null()})
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        unsafe {
            command.pre_exec(move || {
                if libc::setsid() < 0 || libc::fchdir(cwd.as_raw_fd()) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let mut child = command
            .spawn()
            .map_err(|_| RacpError::new("EXECUTION_FAILED"))?;
        let pid = child.id();
        let input=child.stdin.take().map(|f| {let fd:OwnedFd=f.into();File::from(fd)});
        let output: OwnedFd = child.stdout.take().unwrap().into();
        let error: OwnedFd = child.stderr.take().unwrap().into();
        let stdout = File::from(output);
        let stderr = File::from(error);
        for f in [&stdout, &stderr] {
            let flags = unsafe { libc::fcntl(f.as_raw_fd(), libc::F_GETFL) };
            if flags < 0
                || unsafe { libc::fcntl(f.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) }
                    < 0
            {
                unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
                let _ = child.wait();
                return Err(RacpError::new("EXECUTION_FAILED"));
            }
        }
        Ok((Self {
            child,
            pid,
            stdout: Pipe {
                file: stdout,
                eof: false,
            },
            stderr: Pipe {
                file: stderr,
                eof: false,
            },
        },input))
    }
    pub fn pid(&self) -> u32 {
        self.pid
    }
    pub fn job_name(&self) -> Option<&str> {
        None
    }
    pub fn tree_empty(&self) -> Result<bool, RacpError> {
        Ok(true)
    }
    pub fn poll(&mut self) -> Result<Option<i64>, RacpError> {
        use std::os::unix::process::ExitStatusExt;
        Ok(self.child.try_wait()?.map(|s| {
            s.code()
                .map(i64::from)
                .unwrap_or_else(|| -(s.signal().unwrap_or(1) as i64))
        }))
    }
    pub fn kill_tree(&mut self) -> Result<(), RacpError> {
        if unsafe { libc::kill(-(self.pid as i32), libc::SIGKILL) } < 0
            && std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
        {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        Ok(())
    }
}
#[cfg(unix)]
impl Drop for OwnedProcess {
    fn drop(&mut self) {
        let _ = self.kill_tree();
        let until = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while matches!(self.child.try_wait(), Ok(None)) && std::time::Instant::now() < until {
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
    }
}
#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub use windows::OwnedProcess;
