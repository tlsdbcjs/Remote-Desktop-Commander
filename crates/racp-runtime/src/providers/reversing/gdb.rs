//! Allowlisted MI driver. Child containment is established before GDB starts.
use super::mi;
use crate::{
    identity::ProtectedProcess,
    providers::containment::{self, CommandSpec, OwnedProcess},
};
use racp_contract::RacpError;
use racp_core::{validate_local_path, Workspaces};
use serde_json::{json, Value};
use std::{
    fs::File,
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
pub struct Gdb {
    process: OwnedProcess,
    input: File,
    _protection: ProtectedProcess,
    buffer: Vec<u8>,
    token: u64,
    log_bytes: usize,
    pub state: String,
    pub reason: String,
    pub sequence: u64,
    pub pid: Option<u32>,
    pub birth: Option<f64>,
    pub utf8: bool,
}
fn quote(value: &str) -> Result<String, RacpError> {
    if value.contains(['\0', '\r', '\n']) {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    Ok(serde_json::to_string(value)?)
}
impl Gdb {
    pub fn start(
        tool: &Path,
        cwd: &Path,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<Self, RacpError> {
        validate_local_path(tool)?;
        validate_local_path(cwd)?;
        let guards = Workspaces::new(cwd, &[])?;
        let (process, input) = OwnedProcess::spawn_stdio(CommandSpec {
            argv: vec![
                tool.to_string_lossy().into_owned(),
                "-nx".into(),
                "-nh".into(),
                "--interpreter=mi2".into(),
                "-q".into(),
            ],
            environment: containment::environment(&Value::Null)?,
            cwd: guards.directory("default", cwd)?,
        })?;
        let protection = ProtectedProcess::register(process.pid())?;
        let mut driver = Self {
            process,
            input,
            _protection: protection,
            buffer: vec![],
            token: 0,
            log_bytes: 0,
            state: "STARTING".into(),
            reason: "initializing".into(),
            sequence: 0,
            pid: None,
            birth: None,
            utf8: false,
        };
        for command in [
            "-gdb-set auto-load off",
            "-gdb-set confirm off",
            "-gdb-set pagination off",
            "-gdb-set print elements 256",
            "-gdb-set mi-async on",
            "-gdb-set non-stop off",
        ] {
            driver.command(command, deadline, cancel)?;
        }
        driver.utf8 = driver
            .command("-gdb-set host-charset UTF-8", deadline, cancel)
            .is_ok()
            && driver
                .command("-gdb-set target-charset UTF-8", deadline, cancel)
                .is_ok();
        Ok(driver)
    }
    fn observe(&mut self, r: &mi::Record) -> Result<(), RacpError> {
        if r.kind == b'=' && r.name == "thread-group-started" {
            let pid = r.data["pid"]
                .as_str()
                .and_then(|p| p.parse::<u32>().ok())
                .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?;
            self.pid = Some(pid);
            self.birth = Some(crate::identity::process_created(pid)?);
        }
        if r.kind == b'*' && r.name == "running" {
            self.state = "RUNNING".into();
            self.reason = "running".into();
        }
        if r.kind == b'*' && r.name == "stopped" {
            let reason = r.data["reason"].as_str().unwrap_or("stopped");
            self.reason = reason.chars().take(256).collect();
            self.state = if reason.starts_with("exited") {
                "EXITED"
            } else {
                "STOPPED"
            }
            .into();
            self.sequence = self
                .sequence
                .checked_add(1)
                .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?;
        }
        Ok(())
    }
    fn pump(
        &mut self,
        deadline: Instant,
        cancel: &CancellationToken,
        token: Option<&str>,
    ) -> Result<Option<Value>, RacpError> {
        loop {
            if cancel.is_cancelled() {
                return Err(RacpError::new("CANCELLED"));
            }
            if Instant::now() >= deadline {
                return Err(RacpError::new("TIMEOUT"));
            }
            let mut chunk = [0u8; 8192];
            if let Some(n) = self.process.stderr.read_available(&mut chunk)? {
                self.log_bytes += n;
            }
            if let Some(n) = self.process.stdout.read_available(&mut chunk)? {
                self.buffer.extend_from_slice(&chunk[..n]);
            }
            if self.buffer.len() > 1024 * 1024 || self.log_bytes > 8 * 1024 * 1024 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            while let Some(end) = self.buffer.iter().position(|b| *b == b'\n') {
                let line: Vec<_> = self.buffer.drain(..=end).collect();
                if line.starts_with(b"(gdb)") {
                    continue;
                }
                let first = line
                    .iter()
                    .position(|b| !b.is_ascii_digit())
                    .unwrap_or(line.len());
                if !line.get(first).is_some_and(|b| b"^*+=~@&".contains(b)) {
                    self.log_bytes += line.len();
                    continue;
                }
                let record = mi::parse(&line)?;
                if record.kind == b'^' {
                    if record.token.as_deref() != token || token.is_none() {
                        return Err(RacpError::new("GDB_PROTOCOL_ERROR"));
                    }
                    if record.name == "error" {
                        return Err(RacpError::new("GDB_OPERATION_FAILED"));
                    }
                    return Ok(Some(record.data));
                }
                if matches!(record.kind, b'~' | b'@' | b'&') {
                    self.log_bytes += line.len();
                } else {
                    self.observe(&record)?;
                }
            }
            if self.process.poll()?.is_some() {
                self.state = "FAILED".into();
                return Err(RacpError::new("GDB_TRANSPORT_FAILED"));
            }
            if token.is_none() {
                return Ok(None);
            }
            std::thread::sleep(Duration::from_millis(3));
        }
    }
    pub fn command(
        &mut self,
        command: &str,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        if command.len() > 32768 || command.contains(['\0', '\r', '\n']) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.token = self
            .token
            .checked_add(1)
            .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?;
        let token = self.token.to_string();
        let mut stdin = self.input.try_clone()?;
        let bytes = format!("{token}{command}\n").into_bytes();
        let writer = std::thread::spawn(move || stdin.write_all(&bytes));
        let mut result = self
            .pump(deadline, cancel, Some(&token))
            .and_then(|v| v.ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR")));
        while !writer.is_finished() {
            if cancel.is_cancelled() || Instant::now() >= deadline {
                result = Err(RacpError::new("EXECUTION_UNKNOWN"));
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        if result.is_err() && !matches!(&result,Err(e) if e.code.0=="GDB_OPERATION_FAILED") {
            self.process.kill_tree()?;
            self.state = "FAILED".into();
        }
        writer
            .join()
            .map_err(|_| RacpError::new("GDB_TRANSPORT_FAILED"))??;
        result
    }
    pub fn launch(
        &mut self,
        target: &Path,
        args: &[String],
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<(), RacpError> {
        validate_local_path(target)?;
        let path = target
            .to_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        if (!path.is_ascii() || args.iter().any(|s| !s.is_ascii())) && !self.utf8 {
            return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
        }
        self.command(
            &format!("-file-exec-and-symbols {}", quote(path)?),
            deadline,
            cancel,
        )?;
        let quoted: Vec<_> = args.iter().map(|s| quote(s)).collect::<Result<_, _>>()?;
        self.command(
            &format!("-exec-arguments {}", quoted.join(" ")),
            deadline,
            cancel,
        )?;
        self.command("-exec-run --start", deadline, cancel)?;
        while self.pid.is_none() || self.state != "STOPPED" {
            self.pump(deadline, cancel, None)?;
            std::thread::sleep(Duration::from_millis(3));
        }
        Ok(())
    }
    pub fn observe_pending(
        &mut self,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<(), RacpError> {
        self.pump(deadline, cancel, None).map(|_| ())
    }
    pub fn info(&self) -> Value {
        json!({"debugger_state":self.state,"stop_sequence":self.sequence.to_string(),"stop_reason":self.reason,"pid":self.pid,"create_time":self.birth,"utf8_arguments_supported":self.utf8})
    }
    pub fn execute(
        &mut self,
        operation: &str,
        p: &Value,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        self.pump(deadline, cancel, None)?;
        if matches!(
            operation,
            "debugger.registers" | "debugger.read_memory" | "debugger.backtrace"
        ) && self.state != "STOPPED"
        {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        match operation {
            "debugger.info" | "debugger.keepalive" => Ok(self.info()),
            "debugger.wait" => {
                let after = p["after_sequence"]
                    .as_str()
                    .and_then(|s| s.parse::<u64>().ok())
                    .unwrap_or(0);
                while self.sequence <= after {
                    self.pump(deadline, cancel, None)?;
                    std::thread::sleep(Duration::from_millis(5));
                }
                Ok(self.info())
            }
            "debugger.command" => {
                let action = p["action"].as_str().unwrap_or("");
                let command = match action {
                    "continue" => "-exec-continue".into(),
                    "step_into" => "-exec-step".into(),
                    "step_over" => "-exec-next".into(),
                    "interrupt" => "-exec-interrupt --all".into(),
                    "set_breakpoint" => {
                        let location = if let Some(s) = p["symbol"].as_str() {
                            s.into()
                        } else {
                            format!("*{}", p["address"].as_str().unwrap_or(""))
                        };
                        format!("-break-insert {location}")
                    }
                    "remove_breakpoint" => {
                        let n = p["breakpoint_id"]
                            .as_str()
                            .unwrap_or("")
                            .strip_prefix("bp_")
                            .filter(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
                            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
                        format!("-break-delete {n}")
                    }
                    _ => return Err(RacpError::new("OPERATION_NOT_SUPPORTED")),
                };
                let value = self.command(&command, deadline, cancel)?;
                let mut result = self.info();
                result["accepted"] = json!(true);
                if action == "set_breakpoint" {
                    result["breakpoint_id"] = json!(format!(
                        "bp_{}",
                        value["bkpt"]["number"]
                            .as_str()
                            .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?
                    ));
                }
                if action == "remove_breakpoint" {
                    result["removed"] = json!(true);
                }
                Ok(result)
            }
            "debugger.registers" => {
                let names = self.command("-data-list-register-names", deadline, cancel)?;
                let allowed = [
                    "rip", "rsp", "rbp", "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "eflags", "eip",
                    "esp", "ebp", "eax", "ebx", "ecx", "edx", "esi", "edi", "r8", "r9", "r10",
                    "r11", "r12", "r13", "r14", "r15",
                ];
                let names = names["register-names"]
                    .as_array()
                    .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?;
                let numbers: Vec<_> = names
                    .iter()
                    .enumerate()
                    .filter(|(_, n)| n.as_str().is_some_and(|n| allowed.contains(&n)))
                    .map(|(i, _)| i.to_string())
                    .collect();
                let data = self.command(
                    &format!("-data-list-register-values x {}", numbers.join(" ")),
                    deadline,
                    cancel,
                )?;
                let mut registers = serde_json::Map::new();
                for row in data["register-values"].as_array().into_iter().flatten() {
                    let i = row["number"]
                        .as_str()
                        .and_then(|s| s.parse::<usize>().ok())
                        .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?;
                    let name = names
                        .get(i)
                        .and_then(Value::as_str)
                        .filter(|n| allowed.contains(n))
                        .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?;
                    registers.insert(name.into(), row["value"].clone());
                }
                Ok(json!({"registers":registers}))
            }
            "debugger.read_memory" => {
                let size = p["size_bytes"].as_u64().unwrap_or(4096);
                if size > 16384 {
                    return Err(RacpError::new("INVALID_ARGUMENT"));
                }
                let value = self.command(
                    &format!(
                        "-data-read-memory-bytes {} {size}",
                        p["address"].as_str().unwrap_or("")
                    ),
                    deadline,
                    cancel,
                )?;
                let mut bytes = String::new();
                for row in value["memory"].as_array().into_iter().flatten() {
                    bytes.push_str(
                        row["contents"]
                            .as_str()
                            .ok_or_else(|| RacpError::new("GDB_PROTOCOL_ERROR"))?,
                    );
                }
                if bytes.len() != size as usize * 2 || !bytes.bytes().all(|c| c.is_ascii_hexdigit())
                {
                    return Err(RacpError::new("GDB_PROTOCOL_ERROR"));
                }
                Ok(json!({"bytes_hex":bytes}))
            }
            "debugger.backtrace" => {
                let value = self.command("-stack-list-frames 0 63", deadline, cancel)?;
                let frames: Vec<_> = value["stack"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|v| v.get("frame").unwrap_or(v).clone())
                    .collect();
                Ok(json!({"frames":frames}))
            }
            "debugger.close" => {
                self.close()?;
                Ok(json!({"closed":true,"debugger_state":"EXITED"}))
            }
            _ => Err(RacpError::new("OPERATION_NOT_SUPPORTED")),
        }
    }
    pub fn close(&mut self) -> Result<(), RacpError> {
        let cancel = CancellationToken::new();
        let _ = self.command(
            "-gdb-exit",
            Instant::now() + Duration::from_millis(500),
            &cancel,
        );
        self.process.kill_tree()?;
        self.state = "EXITED".into();
        Ok(())
    }
}
impl Drop for Gdb {
    fn drop(&mut self) {
        let _ = self.process.kill_tree();
    }
}
pub fn target_metadata(path: &Path) -> Result<Value, RacpError> {
    validate_local_path(path)?;
    let mut file = File::open(path)?;
    if file.metadata()?.len() > 1024 * 1024 * 1024 {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let mut header = vec![0u8; 65536];
    let n = file.read(&mut header)?;
    header.truncate(n);
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    hash.update(&header);
    let mut buffer = [0u8; 262144];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    let u16at = |i: usize| -> Result<u16, RacpError> {
        Ok(u16::from_le_bytes(
            header
                .get(i..i + 2)
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
                .try_into()
                .unwrap(),
        ))
    };
    let (arch, base) = if header.starts_with(b"MZ") {
        let offset = u32::from_le_bytes(
            header
                .get(60..64)
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
                .try_into()
                .unwrap(),
        ) as usize;
        if header.get(offset..offset + 4) != Some(b"PE\0\0") {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let machine = u16at(offset + 4)?;
        let magic = u16at(offset + 24)?;
        let base = if magic == 0x20b {
            u64::from_le_bytes(
                header
                    .get(offset + 48..offset + 56)
                    .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
                    .try_into()
                    .unwrap(),
            )
        } else if magic == 0x10b {
            u32::from_le_bytes(
                header
                    .get(offset + 52..offset + 56)
                    .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
                    .try_into()
                    .unwrap(),
            ) as u64
        } else {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        };
        (
            match machine {
                0x8664 => "x86_64",
                0x14c => "x86",
                0xaa64 => "aarch64",
                _ => "unknown",
            },
            base,
        )
    } else if header.starts_with(b"\x7fELF") {
        let bytes: [u8; 2] = header
            .get(18..20)
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
            .try_into()
            .unwrap();
        let machine = if header.get(5) == Some(&1) {
            u16::from_le_bytes(bytes)
        } else {
            u16::from_be_bytes(bytes)
        };
        (
            match machine {
                62 => "x86_64",
                3 => "x86",
                183 => "aarch64",
                _ => "unknown",
            },
            0,
        )
    } else {
        return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
    };
    Ok(
        json!({"target_sha256":format!("{:x}",hash.finalize()),"architecture":arch,"image_base":format!("0x{base:x}")}),
    )
}

/// Inspect the installed tool using the same contained native process boundary as execution.
pub fn probe(
    tool: &Path,
    cwd: &Path,
    expected: &str,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<String, RacpError> {
    let guards = Workspaces::new(cwd, &[])?;
    let mut process = OwnedProcess::spawn(CommandSpec {
        argv: vec![tool.to_string_lossy().into_owned(), "--version".into()],
        environment: containment::environment(&Value::Null)?,
        cwd: guards.directory("default", cwd)?,
    })?;
    let _protection = ProtectedProcess::register(process.pid())?;
    let result = (|| {
        let mut output = vec![];
        let mut stderr = 0;
        let mut buffer = [0u8; 8192];
        loop {
            if cancel.is_cancelled() {
                return Err(RacpError::new("CANCELLED"));
            }
            if Instant::now() >= deadline {
                return Err(RacpError::new("TIMEOUT"));
            }
            for _ in 0..8 {
                match process.stdout.read_available(&mut buffer)? {
                    Some(n) if n > 0 => output.extend_from_slice(&buffer[..n]),
                    _ => break,
                }
            }
            for _ in 0..8 {
                match process.stderr.read_available(&mut buffer)? {
                    Some(n) if n > 0 => stderr += n,
                    _ => break,
                }
            }
            if output.len() + stderr > 65536 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            if process.poll()?.is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        let text =
            std::str::from_utf8(&output).map_err(|_| RacpError::new("PLUGIN_VERSION_MISMATCH"))?;
        let banner = text.lines().next().unwrap_or("").trim_end_matches('\r');
        if !banner.starts_with("GNU gdb")
            || !banner.split_whitespace().any(|part| part == expected) && banner != expected
        {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        Ok(banner.to_owned())
    })();
    process.kill_tree()?;
    if !process.tree_empty()? {
        return Err(RacpError::new("CLEANUP_FAILED"));
    }
    result
}
