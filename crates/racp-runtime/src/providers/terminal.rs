use super::{
    containment::{self, CommandSpec, OwnedProcess},
    Provider,
};
use futures_util::future::BoxFuture;
use racp_contract::{new_id, timestamp, RacpError};
use racp_core::{error_value, AgentSettings, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, VecDeque},
    fs::File,
    io::Write,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
fn error(code: &str) -> Value {
    error_value(
        code,
        "terminal operation rejected or interrupted",
        "not_started",
    )
}
fn native(e: RacpError) -> Value {
    error(e.code.0)
}
pub struct TerminalBuffer {
    data: VecDeque<u8>,
    capacity: usize,
    end: u64,
}
impl TerminalBuffer {
    pub fn new(capacity: usize) -> Self {
        Self {
            data: VecDeque::new(),
            capacity,
            end: 0,
        }
    }
    pub fn append(&mut self, raw: &[u8]) {
        self.end += raw.len() as u64;
        let raw = &raw[raw.len().saturating_sub(self.capacity)..];
        let excess = (self.data.len() + raw.len()).saturating_sub(self.capacity);
        self.data.drain(..excess.min(self.data.len()));
        self.data.extend(raw);
    }
    pub fn read(&self, cursor: u64, max_bytes: usize, eof: bool) -> Result<Value, Value> {
        let earliest = self.end - self.data.len() as u64;
        if cursor < earliest {
            let mut e = error("CURSOR_EXPIRED");
            e["details"] = json!({"earliest_cursor":earliest.to_string(),"lost_bytes":(earliest-cursor).to_string()});
            return Err(e);
        }
        if cursor > self.end {
            return Err(error("INVALID_ARGUMENT"));
        }
        let raw: Vec<u8> = self
            .data
            .iter()
            .skip((cursor - earliest) as usize)
            .take(max_bytes)
            .copied()
            .collect();
        let final_chunk = eof && cursor + raw.len() as u64 == self.end;
        let (mut pos, mut replacements, mut text) = (0, 0, String::new());
        let mut pending = false;
        while pos < raw.len() {
            match std::str::from_utf8(&raw[pos..]) {
                Ok(valid) => {
                    text.push_str(valid);
                    pos = raw.len();
                }
                Err(e) => {
                    let valid = e.valid_up_to();
                    text.push_str(std::str::from_utf8(&raw[pos..pos + valid]).unwrap());
                    pos += valid;
                    if let Some(n) = e.error_len() {
                        text.push('\u{fffd}');
                        replacements += 1;
                        pos += n;
                    } else if final_chunk {
                        text.push('\u{fffd}');
                        replacements += 1;
                        pos = raw.len();
                    } else {
                        pending = true;
                        break;
                    }
                }
            }
        }
        Ok(
            json!({"data":text,"next_cursor":(cursor+pos as u64).to_string(),"earliest_cursor":earliest.to_string(),"lost_bytes":"0","eof":eof&&cursor+pos as u64==self.end,"invalid_byte_replacements":replacements,"required_min_bytes":if pending&&pos==0{Some(4)}else{None}}),
        )
    }
}
struct State {
    handle: Value,
    buffer: TerminalBuffer,
    cols: u16,
    rows: u16,
    expires: Instant,
    eof: bool,
    exit: Option<i64>,
    close_result: Option<Value>,
}
impl State {
    fn touch(&mut self) {
        self.expires = Instant::now() + Duration::from_secs(8 * 3600);
        self.handle["last_access_at"] = json!(timestamp());
        self.handle["expires_at"] = json!((chrono::Utc::now() + chrono::Duration::hours(8))
            .to_rfc3339_opts(chrono::SecondsFormat::Micros, true));
    }
    fn revise(&mut self) {
        let previous = self.handle["resource_revision"]
            .as_str()
            .unwrap_or("1")
            .parse::<u64>()
            .unwrap_or(1);
        self.handle["resource_revision"] = json!((previous + 1).to_string());
    }
}
struct Session {
    process: Mutex<Option<OwnedProcess>>,
    input: Arc<Mutex<File>>,
    state: Mutex<State>,
    write: tokio::sync::Mutex<()>,
    closing: AtomicBool,
    changed: tokio::sync::Notify,
}
impl Session {
    fn receive(&self, bytes: &[u8]) {
        if let Ok(mut state) = self.state.lock() {
            state.buffer.append(bytes);
        }
        self.changed.notify_waiters();
    }
    fn tick(&self) -> Result<(), RacpError> {
        let expires = self
            .state
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
            .expires;
        if Instant::now() >= expires {
            return self.close(true).map(|_| ());
        }
        let mut process = self
            .process
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        if let Some(child) = process.as_mut() {
            let mut buffer = [0u8; 65536];
            for _ in 0..16 {
                match child.stdout.read_available(&mut buffer)? {
                    Some(n) if n > 0 => self.receive(&buffer[..n]),
                    _ => break,
                }
            }
            if let Some(code) = child.poll()? {
                child.finish_pty(|bytes| self.receive(bytes))?;
                drop(process.take());
                let mut state = self
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
                state.exit = Some(code);
                state.eof = true;
                state.handle["state"] = json!("CLOSED");
                state.handle["availability"] = json!("unavailable");
                state.revise();
                self.changed.notify_waiters();
            }
        }
        Ok(())
    }
    fn close(&self, expired: bool) -> Result<Value, RacpError> {
        self.closing.store(true, Ordering::SeqCst);
        let mut process = self
            .process
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        if let Some(child) = process.as_mut() {
            let exit = child.finish_pty(|bytes| self.receive(bytes))?;
            drop(process.take());
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
            state.exit = exit;
        }
        let mut state = self
            .state
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        if let Some(result) = &state.close_result {
            return Ok(result.clone());
        }
        state.eof = true;
        state.handle["state"] = json!(if expired { "EXPIRED" } else { "CLOSED" });
        state.handle["availability"] = json!("unavailable");
        state.revise();
        let result = json!({"handle_id":state.handle["id"],"state":state.handle["state"],"process_exit":state.exit,"cleanup_status":"complete","handle":state.handle});
        state.close_result = Some(result.clone());
        self.changed.notify_waiters();
        Ok(result)
    }
}
#[derive(Clone)]
pub struct Terminal {
    guards: Workspaces,
    sessions: Arc<Mutex<BTreeMap<String, Arc<Session>>>>,
    open: Arc<tokio::sync::Mutex<()>>,
    device: String,
    boot: String,
    instance: String,
}
impl Terminal {
    pub fn new(settings: &AgentSettings, boot: &str) -> Result<Self, RacpError> {
        let this = Self {
            guards: Workspaces::new(&settings.workspace, &settings.allowed_workspaces)?,
            sessions: Arc::new(Mutex::new(BTreeMap::new())),
            open: Arc::new(tokio::sync::Mutex::new(())),
            device: settings.device_id.clone(),
            boot: boot.into(),
            instance: new_id("provider"),
        };
        let weak = Arc::downgrade(&this.sessions);
        tokio::spawn(async move {
            loop {
                let Some(map) = weak.upgrade() else { break };
                let _ = tokio::task::spawn_blocking(move || {
                    let sessions: Vec<_> = map
                        .lock()
                        .map(|m| m.values().cloned().collect())
                        .unwrap_or_default();
                    for s in sessions {
                        let _ = s.tick();
                    }
                })
                .await;
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        });
        Ok(this)
    }
    fn identity(&self, request: &Value) -> Result<Arc<Session>, Value> {
        let p = &request["payload"];
        if p["agent_boot_id"].is_string() && p["agent_boot_id"] != self.boot {
            return Err(error("HANDLE_EXPIRED"));
        }
        let id = p["handle_id"].as_str().unwrap_or("");
        let session = self
            .sessions
            .lock()
            .map_err(|_| error("LOCAL_STATE_FAILED"))?
            .get(id)
            .cloned()
            .ok_or_else(|| error("HANDLE_EXPIRED"))?;
        {
            let state = session
                .state
                .lock()
                .map_err(|_| error("LOCAL_STATE_FAILED"))?;
            if state.handle["owner"] != request["context"]["principal_id"]
                || state.handle["workspace_id"] != request["context"]["workspace_id"]
            {
                return Err(error("PERMISSION_DENIED"));
            }
            if state.expires <= Instant::now() || state.handle["state"] == "EXPIRED" {
                return Err(error("HANDLE_EXPIRED"));
            }
        }
        Ok(session)
    }
    fn spawn(
        &self,
        request: &Value,
        cancel: &CancellationToken,
    ) -> Result<(Arc<Session>, Value), RacpError> {
        let p = &request["payload"];
        let workspace = request["context"]["workspace_id"]
            .as_str()
            .unwrap_or("default");
        let principal = request["context"]["principal_id"].as_str().unwrap_or("");
        let argv = if p["argv"].is_array() {
            containment::argv(&json!({"mode":"argv","argv":p["argv"]}))?
        } else {
            let shell =
                p["shell"]
                    .as_str()
                    .unwrap_or(if cfg!(windows) { "powershell" } else { "bash" });
            #[cfg(unix)]
            let args = if shell == "bash" {
                vec![
                    "/bin/bash".into(),
                    "--noprofile".into(),
                    "--norc".into(),
                    "-i".into(),
                ]
            } else {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            };
            #[cfg(windows)]
            let args = {
                let system = std::env::var("SYSTEMROOT").unwrap_or_else(|_| "C:\\Windows".into());
                match shell {
                    "cmd" => vec![format!("{system}\\System32\\cmd.exe"), "/d".into()],
                    "powershell" => vec![
                        format!("{system}\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"),
                        "-NoLogo".into(),
                        "-NoProfile".into(),
                    ],
                    _ => return Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
                }
            };
            args
        };
        let mut environment = containment::environment(&p["env"])?;
        environment
            .entry("TERM".into())
            .or_insert_with(|| "xterm-256color".into());
        let path = self
            .guards
            .path(workspace, p["cwd"].as_str().unwrap_or("."))?;
        let cwd = self.guards.directory(workspace, &path)?;
        let mut map = self
            .sessions
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        map.retain(|_, s| s.state.lock().is_ok_and(|s| s.expires > Instant::now()));
        if map.len() >= 64
            || map
                .values()
                .filter(|s| s.state.lock().is_ok_and(|s| !s.eof))
                .count()
                >= 8
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        let (cols, rows) = (
            p["cols"].as_u64().unwrap_or(120) as u16,
            p["rows"].as_u64().unwrap_or(40) as u16,
        );
        let (process, input) = OwnedProcess::spawn_pty(
            CommandSpec {
                argv,
                environment,
                cwd,
            },
            cols,
            rows,
        )?;
        let pid = process.pid();
        let id = new_id("term");
        let now = timestamp();
        let handle = json!({"id":id,"type":"terminal","device_id":self.device,"owner":principal,"agent_boot_id":self.boot,"workspace_id":workspace,"provider_instance_id":self.instance,"resource_revision":"1","created_at":now,"last_access_at":now,"expires_at":now,"state":"ACTIVE","availability":"available"});
        let mut state = State {
            handle,
            buffer: TerminalBuffer::new(4 * 1024 * 1024),
            cols,
            rows,
            expires: Instant::now(),
            eof: false,
            exit: None,
            close_result: None,
        };
        state.touch();
        let result = json!({"handle_id":id,"handle":state.handle,"pid":pid,"cols":cols,"rows":rows,"cursor":"0","streams":"merged"});
        let session = Arc::new(Session {
            process: Mutex::new(Some(process)),
            input: Arc::new(Mutex::new(input)),
            state: Mutex::new(state),
            write: tokio::sync::Mutex::new(()),
            closing: AtomicBool::new(false),
            changed: tokio::sync::Notify::new(),
        });
        if cancel.is_cancelled() {
            session.close(false)?;
            return Err(RacpError::new("CANCELLED"));
        }
        map.insert(id, session.clone());
        Ok((session, result))
    }
    fn read_session(session: &Session, p: &Value) -> Result<Value, Value> {
        let cursor = p["cursor"]
            .as_str()
            .unwrap_or("0")
            .parse::<u64>()
            .map_err(|_| error("INVALID_ARGUMENT"))?;
        let state = session
            .state
            .lock()
            .map_err(|_| error("LOCAL_STATE_FAILED"))?;
        let mut result = state.buffer.read(
            cursor,
            p["max_bytes"].as_u64().unwrap_or(65536) as usize,
            state.eof,
        )?;
        result["handle_id"] = state.handle["id"].clone();
        result["process_exit"] = json!(state.exit);
        result["handle"] = state.handle.clone();
        Ok(result)
    }
    pub fn stream_read(&self, request: &Value) -> Result<Value, Value> {
        let session = self.identity(request)?;
        Self::read_session(&session, &request["payload"])
    }
    async fn close_session(session: Arc<Session>, expired: bool) -> Result<Value, Value> {
        tokio::task::spawn_blocking(move || session.close(expired))
            .await
            .map_err(|_| error("CLEANUP_FAILED"))?
            .map_err(native)
    }
    async fn run(&self, request: Value, cancel: CancellationToken) -> Result<Value, Value> {
        if cancel.is_cancelled() {
            return Err(error("CANCELLED"));
        }
        let operation = request["operation"].as_str().unwrap_or("");
        let p = &request["payload"];
        let deadline = tokio::time::Instant::now()
            + Duration::from_millis(request["remaining_timeout_ms"].as_u64().unwrap_or(1));
        if operation == "terminal.open" {
            let _gate = self.open.lock().await;
            let this = self.clone();
            let canceled = cancel.clone();
            return tokio::task::spawn_blocking(move || {
                this.spawn(&request, &canceled).map(|(_, result)| result)
            })
            .await
            .map_err(|_| error("EXECUTION_UNKNOWN"))?
            .map_err(native);
        }
        let session = self.identity(&request)?;
        if operation == "terminal.read" {
            let mut result = Self::read_session(&session, p)?;
            let wait = p["wait_ms"].as_u64().unwrap_or(0);
            if result["next_cursor"] == p["cursor"] && result["eof"] != true && wait > 0 {
                let wait_until =
                    (tokio::time::Instant::now() + Duration::from_millis(wait)).min(deadline);
                loop {
                    let changed = session.changed.notified();
                    tokio::select! {_=cancel.cancelled()=>return Err(error("CANCELLED")),_=tokio::time::sleep_until(wait_until)=>break,_=changed=>{}}
                    result = Self::read_session(&session, p)?;
                    if result["next_cursor"] != p["cursor"] || result["eof"] == true {
                        break;
                    }
                }
                result = Self::read_session(&session, p)?;
            }
            return Ok(result);
        }
        let _serial = session.write.lock().await;
        if operation == "terminal.close" {
            return Self::close_session(session.clone(), false).await;
        }
        if session.closing.load(Ordering::SeqCst)
            || session
                .state
                .lock()
                .map_err(|_| error("LOCAL_STATE_FAILED"))?
                .eof
        {
            return Err(error("HANDLE_EXPIRED"));
        }
        if cancel.is_cancelled() {
            return Err(error("CANCELLED"));
        }
        if operation == "terminal.write" {
            use base64::Engine;
            let data = p["data"].as_str().unwrap_or("");
            let raw = if p["encoding"] == "base64" {
                base64::engine::general_purpose::STANDARD
                    .decode(data)
                    .map_err(|_| error("INVALID_ARGUMENT"))?
            } else {
                data.as_bytes().to_vec()
            };
            if raw.len() > 65536 {
                return Err(error("INVALID_ARGUMENT"));
            }
            let accepted = Arc::new(AtomicUsize::new(0));
            let written = accepted.clone();
            let target = session.clone();
            let signal = cancel.clone();
            let mut writer = tokio::task::spawn_blocking(move || -> Result<(), RacpError> {
                let mut input = target
                    .input
                    .lock()
                    .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
                let mut offset = 0;
                while offset < raw.len() {
                    if signal.is_cancelled() || target.closing.load(Ordering::SeqCst) {
                        return Err(RacpError::new("CANCELLED"));
                    }
                    match input.write(&raw[offset..(offset + 1024).min(raw.len())]) {
                        Ok(0) => return Err(RacpError::new("EXECUTION_UNKNOWN")),
                        Ok(n) => {
                            offset += n;
                            written.store(offset, Ordering::SeqCst);
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(5))
                        }
                        Err(_) => return Err(RacpError::new("EXECUTION_UNKNOWN")),
                    }
                }
                Ok(())
            });
            let code = tokio::select! {result=&mut writer=>{match result{Ok(Ok(()))=>None,_=>Some("EXECUTION_UNKNOWN")}},_=cancel.cancelled()=>Some("CANCELLED"),_=tokio::time::sleep_until(deadline)=>Some("TIMEOUT")};
            if let Some(code) = code {
                let closed = Self::close_session(session.clone(), false).await;
                let cleanup = if closed.is_ok() {
                    "complete"
                } else {
                    "unknown"
                };
                if !writer.is_finished() {
                    let _ = tokio::time::timeout(Duration::from_secs(5), writer).await;
                }
                let mut e = error(code);
                e["execution_state"] = json!("completed");
                e["details"] = json!({"accepted_bytes":accepted.load(Ordering::SeqCst),"accepted_bytes_exact":false,"cleanup_status":cleanup,"handle_id":p["handle_id"]});
                return Err(e);
            }
            session
                .state
                .lock()
                .map_err(|_| error("LOCAL_STATE_FAILED"))?
                .touch();
            return Ok(
                json!({"handle_id":p["handle_id"],"accepted_bytes":accepted.load(Ordering::SeqCst),"accepted_bytes_exact":true}),
            );
        }
        if operation == "terminal.resize" {
            let (cols, rows) = (
                p["cols"].as_u64().unwrap_or(120) as u16,
                p["rows"].as_u64().unwrap_or(40) as u16,
            );
            let target = session.clone();
            tokio::task::spawn_blocking(move || {
                target
                    .process
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                    .as_ref()
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?
                    .resize(cols, rows)
            })
            .await
            .map_err(|_| error("EXECUTION_UNKNOWN"))?
            .map_err(native)?;
            let mut state = session
                .state
                .lock()
                .map_err(|_| error("LOCAL_STATE_FAILED"))?;
            state.cols = cols;
            state.rows = rows;
            state.revise();
        } else if operation != "terminal.keepalive" {
            return Err(error("CAPABILITY_UNAVAILABLE"));
        }
        let mut state = session
            .state
            .lock()
            .map_err(|_| error("LOCAL_STATE_FAILED"))?;
        state.touch();
        Ok(
            json!({"handle_id":p["handle_id"],"cols":state.cols,"rows":state.rows,"handle":state.handle}),
        )
    }
}
impl Provider for Terminal {
    fn stream_read(&self, request: &Value) -> Result<Value, Value> {
        Terminal::stream_read(self, request)
    }
    fn capabilities(&self) -> Vec<Value> {
        vec![
            json!({"name":"terminal","version":"1.0.0","operations":["terminal.open","terminal.read","terminal.write","terminal.resize","terminal.close","terminal.keepalive"],"installed":true,"supported":true,"enabled":true,"healthy":true,"unavailable_reason":null,"attributes":{"backend":if cfg!(windows){"conpty"}else{"posix_pty"},"streams":"merged","buffer_bytes":4*1024*1024,"max_active":8}}),
        ]
    }
    fn inventory(&self) -> Vec<Value> {
        self.sessions
            .lock()
            .map(|map| {
                map.values()
                    .filter_map(|s| s.state.lock().ok().map(|s| s.handle.clone()))
                    .collect()
            })
            .unwrap_or_default()
    }
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        Box::pin(async move {
            Ok(match self.run(request, cancel).await {
                Ok(result) => json!({"state":"SUCCEEDED","result":result,"error":null}),
                Err(e) => {
                    json!({"state":match e["code"].as_str(){Some("CANCELLED")=>"CANCELLED",Some("TIMEOUT")=>"TIMED_OUT",Some("EXECUTION_UNKNOWN")=>"UNKNOWN",_=>"FAILED"},"result":null,"error":e})
                }
            })
        })
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        let map = self.sessions.clone();
        Box::pin(async move {
            let sessions: Vec<_> = map
                .lock()
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
                .values()
                .cloned()
                .collect();
            for session in sessions {
                Self::close_session(session, true)
                    .await
                    .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
            }
            Ok(())
        })
    }
}
