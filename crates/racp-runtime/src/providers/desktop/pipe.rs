//! Local message-mode Named Pipe. Mutual HMAC proofs are bound to pinned OS peers.
use super::{
    decode_pipe_message, encode_pipe_message,
    native_identity::{client_scope, PinnedPeer},
    PairConfig, PeerIdentity, MAX_PIPE_MESSAGE,
};
use racp_contract::RacpError;
use serde_json::{json, Value};
use std::{
    mem::size_of,
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, SECURITY_ATTRIBUTES},
    Storage::FileSystem::*,
    System::{Pipes::*, Threading::*, IO::*},
};

fn wide(value: &str) -> Vec<u16> {
    std::ffi::OsStr::new(value)
        .encode_wide()
        .chain(Some(0))
        .collect()
}
fn nonce() -> String {
    let bytes: [u8; 32] = rand::random();
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn fields(value: &Value, expected: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|m| m.len() == expected.len() && expected.iter().all(|k| m.contains_key(*k)))
}
struct Pipe {
    handle: OwnedHandle,
    deadline: Instant,
}
impl Pipe {
    fn complete(&self, overlap: &mut OVERLAPPED, pending: bool) -> Result<u32, RacpError> {
        let raw = self.handle.as_raw_handle();
        if pending {
            let remaining = self
                .deadline
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(u32::MAX as u128 - 1) as u32;
            if unsafe { WaitForSingleObject(overlap.hEvent, remaining) } != WAIT_OBJECT_0 {
                let mut size = 0;
                unsafe {
                    CancelIoEx(raw, overlap);
                    GetOverlappedResult(raw, overlap, &mut size, 1);
                }
                return Err(RacpError::new("TIMEOUT"));
            }
        }
        let mut size = 0;
        if unsafe { GetOverlappedResult(raw, overlap, &mut size, 0) } == 0 {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
        Ok(size)
    }
    fn overlap() -> Result<(OVERLAPPED, OwnedHandle), RacpError> {
        let event = unsafe { CreateEventW(std::ptr::null(), 1, 0, std::ptr::null()) };
        if event.is_null() {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let event = unsafe { OwnedHandle::from_raw_handle(event) };
        let overlap = OVERLAPPED {
            hEvent: event.as_raw_handle(),
            ..Default::default()
        };
        Ok((overlap, event))
    }
    fn read(&self) -> Result<Value, RacpError> {
        if Instant::now() >= self.deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        let mut raw = vec![0u8; MAX_PIPE_MESSAGE + 1];
        let (mut overlap, _event) = Self::overlap()?;
        let success = unsafe {
            ReadFile(
                self.handle.as_raw_handle(),
                raw.as_mut_ptr(),
                raw.len() as u32,
                std::ptr::null_mut(),
                &mut overlap,
            )
        };
        let pending = success == 0 && unsafe { GetLastError() } == ERROR_IO_PENDING;
        if success == 0 && !pending {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
        let size = self.complete(&mut overlap, pending)? as usize;
        if size == 0 || size > MAX_PIPE_MESSAGE {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        decode_pipe_message(&raw[..size])
    }
    fn write(&self, value: &Value) -> Result<(), RacpError> {
        if Instant::now() >= self.deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        let raw = encode_pipe_message(value)?;
        let (mut overlap, _event) = Self::overlap()?;
        let success = unsafe {
            WriteFile(
                self.handle.as_raw_handle(),
                raw.as_ptr(),
                raw.len() as u32,
                std::ptr::null_mut(),
                &mut overlap,
            )
        };
        let pending = success == 0 && unsafe { GetLastError() } == ERROR_IO_PENDING;
        if success == 0 && !pending {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
        if self.complete(&mut overlap, pending)? as usize != raw.len() {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
        Ok(())
    }
}
pub struct PipeServer {
    config: PairConfig,
    pipe: Pipe,
}
impl PipeServer {
    pub fn new(config: PairConfig) -> Result<Self, RacpError> {
        config.validate()?;
        config.require_broker(PinnedPeer::open(std::process::id())?.identity())?;
        let descriptor = wide(&format!(
            "D:P(A;;GA;;;{})(A;;0x100103;;;{})",
            config.user_sid,
            config.agent_acl_sid()
        ));
        let mut security = std::ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                descriptor.as_ptr(),
                SDDL_REVISION_1,
                &mut security,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: security,
            bInheritHandle: 0,
        };
        let name = wide(&config.pipe());
        let raw = unsafe {
            CreateNamedPipeW(
                name.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                (MAX_PIPE_MESSAGE + 1) as u32,
                (MAX_PIPE_MESSAGE + 1) as u32,
                0,
                &attributes,
            )
        };
        unsafe {
            LocalFree(security);
        }
        if raw == INVALID_HANDLE_VALUE {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(Self {
            config,
            pipe: Pipe {
                handle: unsafe { OwnedHandle::from_raw_handle(raw) },
                deadline: Instant::now(),
            },
        })
    }
    pub fn accept(
        &mut self,
        handler: impl FnOnce(Value) -> Result<Value, RacpError>,
    ) -> Result<bool, RacpError> {
        self.pipe.deadline = Instant::now() + Duration::from_millis(250);
        let (mut overlap, _event) = Pipe::overlap()?;
        let success = unsafe { ConnectNamedPipe(self.pipe.handle.as_raw_handle(), &mut overlap) };
        if success == 0 {
            match unsafe { GetLastError() } {
                ERROR_PIPE_CONNECTED => (),
                ERROR_IO_PENDING => match self.pipe.complete(&mut overlap, true) {
                    Ok(_) => (),
                    Err(e) if e.code.0 == "TIMEOUT" => {
                        unsafe {
                            DisconnectNamedPipe(self.pipe.handle.as_raw_handle());
                        }
                        return Ok(false);
                    }
                    Err(e) => {
                        unsafe {
                            DisconnectNamedPipe(self.pipe.handle.as_raw_handle());
                        }
                        return Err(e);
                    }
                },
                _ => return Err(RacpError::new("EXECUTION_UNKNOWN")),
            }
        }
        let result = self.exchange(handler);
        unsafe {
            DisconnectNamedPipe(self.pipe.handle.as_raw_handle());
        }
        result.map(|_| true)
    }
    fn exchange(
        &mut self,
        handler: impl FnOnce(Value) -> Result<Value, RacpError>,
    ) -> Result<(), RacpError> {
        self.pipe.deadline = Instant::now() + Duration::from_secs(3);
        let initial = self.pipe.read()?;
        let mut pid = 0;
        if unsafe { GetNamedPipeClientProcessId(self.pipe.handle.as_raw_handle(), &mut pid) } == 0 {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let peer = PinnedPeer::open(pid)?;
        self.config.require_agent(peer.identity())?;
        let (sid, session) = client_scope(self.pipe.handle.as_raw_handle())?;
        if sid != peer.identity().sid || session != peer.identity().session {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if !fields(&initial, &["version", "nonce"]) || initial["version"] != self.config.version {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let client = initial["nonce"]
            .as_str()
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        let server = nonce();
        self.pipe.write(
            &json!({"nonce":server,"proof":self.config.proof("broker", &server, client)?}),
        )?;
        let request = self.pipe.read()?;
        if !fields(&request, &["proof", "request"]) || !request["request"].is_object() {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        self.config.verify(
            "agent",
            &server,
            client,
            request["proof"].as_str().unwrap_or(""),
        )?;
        peer.alive()?;
        let budget = request["request"]["context"]["timeout_ms"]
            .as_u64()
            .unwrap_or(3000);
        if !(1..=30000).contains(&budget) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.pipe.deadline = Instant::now() + Duration::from_millis(budget);
        // client_scope reverted before this closure can inspect a desktop or execute input.
        let response = match handler(request["request"].clone()) {
            Ok(value) => json!({"ok":true,"result":value}),
            Err(e) => json!({"ok":false,"error":{"code":e.code.0,"execution_state":"unknown"}}),
        };
        peer.alive()?;
        self.pipe.write(&response)?;
        if self.pipe.read()? != json!({"received":true}) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        Ok(())
    }
}
pub fn request(
    config: &PairConfig,
    expected_broker: &PeerIdentity,
    value: Value,
    timeout: Duration,
) -> Result<Value, RacpError> {
    config.validate()?;
    config.require_agent(PinnedPeer::open(std::process::id())?.identity())?;
    let deadline = Instant::now() + timeout.min(Duration::from_secs(30));
    let name = wide(&config.pipe());
    let raw = loop {
        let raw = unsafe {
            CreateFileW(
                name.as_ptr(),
                0x100103,
                0,
                std::ptr::null(),
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION,
                std::ptr::null_mut(),
            )
        };
        if raw != INVALID_HANDLE_VALUE {
            break raw;
        }
        let error = unsafe { GetLastError() };
        if !matches!(
            error,
            ERROR_FILE_NOT_FOUND | ERROR_PIPE_BUSY | ERROR_SEM_TIMEOUT
        ) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        unsafe {
            WaitNamedPipeW(name.as_ptr(), 25);
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let pipe = Pipe {
        handle: unsafe { OwnedHandle::from_raw_handle(raw) },
        deadline,
    };
    let mode = PIPE_READMODE_MESSAGE;
    if unsafe { SetNamedPipeHandleState(raw, &mode, std::ptr::null(), std::ptr::null()) } == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut pid = 0;
    if unsafe { GetNamedPipeServerProcessId(raw, &mut pid) } == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let peer = PinnedPeer::open(pid)?;
    config.require_broker(peer.identity())?;
    if peer.identity() != expected_broker {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let client = nonce();
    pipe.write(&json!({"version":config.version,"nonce":client}))?;
    let response = pipe.read()?;
    if !fields(&response, &["nonce", "proof"]) {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let server = response["nonce"].as_str().unwrap_or("");
    config.verify(
        "broker",
        server,
        &client,
        response["proof"].as_str().unwrap_or(""),
    )?;
    peer.alive()?;
    pipe.write(&json!({"proof":config.proof("agent", server, &client)?, "request":value}))?;
    let response = pipe.read()?;
    pipe.write(&json!({"received":true}))?;
    peer.alive()?;
    if response["ok"] == true {
        if !fields(&response, &["ok", "result"]) || !response["result"].is_object() {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
        Ok(response["result"].clone())
    } else {
        // Never echo arbitrary remote error text, commands, or credential material.
        let code = match response["error"]["code"].as_str().unwrap_or("") {
            "SESSION_UNAVAILABLE" => "SESSION_UNAVAILABLE",
            "SESSION_LOCKED" => "SESSION_LOCKED",
            "PERMISSION_DENIED" => "PERMISSION_DENIED",
            "INVALID_ARGUMENT" => "INVALID_ARGUMENT",
            "CAPABILITY_UNAVAILABLE" => "CAPABILITY_UNAVAILABLE",
            "RESOURCE_BUSY" => "RESOURCE_BUSY",
            "STALE_OBSERVATION" => "STALE_OBSERVATION",
            "HANDLE_EXPIRED" => "HANDLE_EXPIRED",
            "INPUT_GUARDIAN_UNAVAILABLE" => "INPUT_GUARDIAN_UNAVAILABLE",
            "TIMEOUT" => "TIMEOUT",
            "CANCELLED" => "CANCELLED",
            "RESOURCE_EXHAUSTED" => "RESOURCE_EXHAUSTED",
            "PRECONDITION_FAILED" => "PRECONDITION_FAILED",
            "CLEANUP_FAILED" => "CLEANUP_FAILED",
            _ => "EXECUTION_UNKNOWN",
        };
        Err(RacpError::new(code))
    }
}
