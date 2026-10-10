use super::{native_identity::PinnedPeer, pipe, PairConfig, PeerIdentity};
use crate::{
    identity::ProtectedProcess,
    providers::{
        containment::{self, CommandSpec, OwnedProcess},
        Provider,
    },
};
use futures_util::future::BoxFuture;
use racp_contract::{new_id, RacpError};
use racp_core::{private_dir, AgentSettings, SecretStore, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::Write,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;

pub(super) enum BrokerProcess {
    Local(OwnedProcess),
    Registered(super::login::BrokerJob),
}
impl BrokerProcess {
    pub(super) fn alive(&mut self) -> bool {
        match self {
            Self::Local(p) => p.poll().is_ok_and(|v| v.is_none()),
            Self::Registered(p) => p.alive(),
        }
    }
    fn kill_tree(&mut self) -> Result<(), RacpError> {
        match self {
            Self::Local(p) => p.kill_tree(),
            Self::Registered(p) => p.kill(),
        }
    }
    fn tree_empty(&mut self) -> Result<bool, RacpError> {
        match self {
            Self::Local(p) => p.tree_empty(),
            Self::Registered(p) => p.empty(),
        }
    }
    fn drain(&mut self) {
        if let Self::Local(p) = self {
            let mut bytes = [0u8; 4096];
            for pipe in [&mut p.stdout, &mut p.stderr] {
                for _ in 0..16 {
                    match pipe.read_available(&mut bytes) {
                        Ok(Some(n)) if n > 0 => (),
                        _ => break,
                    }
                }
            }
        }
    }
}
pub(super) enum GuardianProcess {
    Local(std::process::Child),
    Registered(PinnedPeer),
}
impl GuardianProcess {
    fn alive(&mut self) -> bool {
        match self {
            Self::Local(p) => p.try_wait().is_ok_and(|v| v.is_none()),
            Self::Registered(p) => p.alive().is_ok(),
        }
    }
}
pub(super) struct Child {
    pub(super) guardian: GuardianProcess,
    pub(super) _guardian_protection: ProtectedProcess,
    pub(super) process: BrokerProcess,
    pub(super) _protection: ProtectedProcess,
    pub(super) peer: PeerIdentity,
    pub(super) config: PairConfig,
    pub(super) path: PathBuf,
}
impl Child {
    pub(super) fn stop(&mut self) -> Result<(), RacpError> {
        if self.process.alive() {
            let _ = pipe::request(
                &self.config,
                &self.peer,
                json!({"operation":"broker.stop"}),
                Duration::from_secs(3),
            );
        }
        self.process.kill_tree()?;
        let deadline = Instant::now() + Duration::from_secs(3);
        while !self.process.tree_empty()? {
            if Instant::now() >= deadline {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        racp_core::atomic_write(&self.path.with_file_name("guardian-stop"), b"", true)?;
        while self.guardian.alive() {
            if Instant::now() >= deadline {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let _ = std::fs::remove_file(&self.path);
        Ok(())
    }
}
#[derive(Clone)]
pub struct Desktop {
    child: Arc<Mutex<BTreeMap<u32, Child>>>,
    registrar: Arc<Mutex<Option<super::login::Registrar>>>,
    status: Arc<Mutex<Value>>,
    spool: PathBuf,
    device: String,
    enabled: bool,
}
impl Desktop {
    pub fn new(settings: &AgentSettings) -> Result<Self, RacpError> {
        let spool = settings.data_dir.join("spool");
        private_dir(&spool)?;
        let provider = Self {
            child: Arc::new(Mutex::new(BTreeMap::new())),
            registrar: Arc::new(Mutex::new(None)),
            status: Arc::new(Mutex::new(
                json!({"available":false,"error_code":if settings.desktop_enabled { "SESSION_UNAVAILABLE" } else { "DESKTOP_NOT_CONFIGURED" }}),
            )),
            spool,
            device: settings.device_id.clone(),
            enabled: settings.desktop_enabled,
        };
        if provider.enabled {
            let actor = PinnedPeer::open(std::process::id())?;
            if actor.identity().session == 0 {
                if settings.data_dir.join("service-login.json").try_exists()? {
                    *provider
                        .registrar
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))? = Some(
                        super::login::Registrar::start(settings, provider.child.clone())?,
                    );
                }
                return Ok(provider);
            }
            if let Ok(child) = Self::launch(settings) {
                let state = pipe::request(
                    &child.config,
                    &child.peer,
                    json!({"operation":"broker.status"}),
                    Duration::from_secs(3),
                );
                if let Ok(mut state) = state {
                    state["broker_running"] = json!(true);
                    *provider
                        .status
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))? = state;
                    provider
                        .child
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                        .insert(child.config.session_id, child);
                } else {
                    let mut child = child;
                    let _ = child.process.kill_tree();
                    let _ = std::fs::remove_file(child.path);
                }
            }
        }
        Ok(provider)
    }
    fn launch(settings: &AgentSettings) -> Result<Child, RacpError> {
        let actor = PinnedPeer::open(std::process::id())?.identity().clone();
        if actor.session == 0 {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        let pair_id = new_id("pair").trim_start_matches("pair_").to_owned();
        let root = settings.data_dir.join("desktop").join(&pair_id);
        private_dir(&root)?;
        let path = root.join("pair.bin");
        use base64::Engine;
        let random: [u8; 32] = rand::random();
        let config = PairConfig {
            version: 1,
            pair_id,
            session_id: actor.session,
            user_sid: actor.sid.clone(),
            agent_sid: actor.sid,
            agent_service_sid: None,
            agent_pid: actor.pid,
            agent_created: actor.created,
            agent_session: actor.session,
            secret: base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(random),
            job_name: None,
        };
        config.validate()?;
        SecretStore::new(path.clone()).save(
            &BTreeMap::from([
                ("pair".into(), serde_json::to_string(&config)?),
                ("device_id".into(), settings.device_id.clone()),
            ]),
            false,
        )?;
        use std::os::windows::process::CommandExt;
        use std::process::{Command, Stdio};
        let mut guardian = Command::new(std::env::current_exe()?)
            .args(["guardian", "--pair-config"])
            .arg(&path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x01000000 | 0x08000000)
            .spawn()
            .map_err(|_| RacpError::new("SESSION_UNAVAILABLE"))?;
        let guardian_protection = ProtectedProcess::register(guardian.id())?;
        let guardian_peer = PinnedPeer::open(guardian.id())?;
        config.require_broker(guardian_peer.identity())?;
        let ready_deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if guardian.try_wait()?.is_some() || Instant::now() >= ready_deadline {
                let _ = std::fs::remove_file(&path);
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            if let Ok(bytes) =
                racp_core::read_bounded(&path.with_file_name("guardian-status.json"), 4096, true)
            {
                if let Ok(status) = serde_json::from_slice::<Value>(&bytes) {
                    if status["pid"] == guardian.id()
                        && status["create_time"] == guardian_peer.identity().created
                        && status["healthy"] == true
                        && status["outside_all_jobs"] == true
                    {
                        break;
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let mut values = SecretStore::new(path.clone()).load()?;
        values.insert("guardian_pid".into(), guardian.id().to_string());
        values.insert(
            "guardian_created".into(),
            guardian_peer.identity().created.to_string(),
        );
        SecretStore::new(path.clone()).save(&values, true)?;
        let guards = Workspaces::new(&root, &[])?;
        let spawn = OwnedProcess::spawn(CommandSpec {
            argv: vec![
                std::env::current_exe()?.to_string_lossy().into_owned(),
                "broker".into(),
                "--pair-config".into(),
                path.to_string_lossy().into_owned(),
            ],
            environment: containment::environment(&Value::Null)?,
            cwd: guards.directory("default", &root)?,
        });
        let process = match spawn {
            Ok(process) => process,
            Err(e) => {
                let _ = std::fs::remove_file(&path);
                return Err(e);
            }
        };
        let protection = ProtectedProcess::register(process.pid())?;
        let peer = PinnedPeer::open(process.pid())?.identity().clone();
        config.require_broker(&peer)?;
        Ok(Child {
            guardian: GuardianProcess::Local(guardian),
            _guardian_protection: guardian_protection,
            process: BrokerProcess::Local(process),
            _protection: protection,
            peer,
            config,
            path,
        })
    }
    fn materialize(
        &self,
        child: &Child,
        descriptor: &Value,
        path: &std::path::Path,
        context: &Value,
        deadline: Instant,
        cancel: &CancellationToken,
        maximum: u64,
    ) -> Result<(), RacpError> {
        let size = descriptor["size_bytes"]
            .as_u64()
            .filter(|n| *n > 0 && *n <= maximum)
            .ok_or_else(|| RacpError::new("EXECUTION_UNKNOWN"))?;
        if descriptor["media_type"] != "image/png" {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
        let mut file = racp_core::secure_create_file(path)?;
        use sha2::{Digest, Sha256};
        let mut hash = Sha256::new();
        let copied = (|| {
            let mut offset = 0;
            while offset < size {
                if cancel.is_cancelled() {
                    return Err(RacpError::new("CANCELLED"));
                }
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err(RacpError::new("TIMEOUT"));
                }
                let length = (size - offset).min(32768);
                let result = pipe::request(
                    &child.config,
                    &child.peer,
                    json!({"operation":"broker.capture_read","payload":{"capture_id":descriptor["capture_id"],"offset":offset,"length":length},"context":context}),
                    remaining,
                )?;
                use base64::Engine;
                let bytes = base64::engine::general_purpose::STANDARD
                    .decode(result["data"].as_str().unwrap_or(""))
                    .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
                if bytes.is_empty()
                    || bytes.len() as u64 > length
                    || result["offset"] != offset
                    || result["next_offset"] != offset + bytes.len() as u64
                    || result["eof"] != (offset + bytes.len() as u64 == size)
                    || (offset == 0 && !bytes.starts_with(b"\x89PNG\r\n\x1a\n"))
                {
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
                file.write_all(&bytes)?;
                hash.update(&bytes);
                offset += bytes.len() as u64;
            }
            file.sync_all()?;
            if descriptor["sha256"] != format!("{:x}", hash.finalize()) {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            Ok(())
        })();
        drop(file);
        let _ = pipe::request(
            &child.config,
            &child.peer,
            json!({"operation":"broker.capture_release","payload":{"capture_id":descriptor["capture_id"]},"context":context}),
            Duration::from_millis(500),
        );
        if copied.is_err() {
            let _ = std::fs::remove_file(path);
        }
        copied
    }
    fn execute_native(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> Result<Value, RacpError> {
        let deadline = Instant::now()
            + Duration::from_millis(
                request["remaining_timeout_ms"]
                    .as_u64()
                    .unwrap_or(3000)
                    .clamp(1, 30000),
            );
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        let mut guard = self
            .child
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        guard.retain(|_, child| child.process.alive() || child.guardian.alive());
        if request["operation"] == "desktop.sessions" {
            let mut sessions = vec![];
            for child in guard.values_mut() {
                if child.process.alive() {
                    if let Ok(mut status) = pipe::request(
                        &child.config,
                        &child.peer,
                        json!({"operation":"broker.status"}),
                        Duration::from_secs(1),
                    ) {
                        status["broker_running"] = json!(true);
                        sessions.push(status);
                    }
                }
            }
            return Ok(json!({"sessions":sessions,"configured":self.enabled}));
        }
        let session = request["payload"]["session_id"]
            .as_u64()
            .filter(|n| *n > 0 && *n <= u32::MAX as u64)
            .map(|n| n as u32);
        // Clipboard has no session field; choose only an unambiguous live session.
        let session = match session {
            Some(s) => s,
            None if guard.len() == 1 => *guard.keys().next().unwrap(),
            _ => return Err(RacpError::new("SESSION_UNAVAILABLE")),
        };
        let child = guard
            .get_mut(&session)
            .ok_or_else(|| RacpError::new("SESSION_UNAVAILABLE"))?;
        if !child.process.alive() {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        child.process.drain();
        let mut status = pipe::request(
            &child.config,
            &child.peer,
            json!({"operation":"broker.status"}),
            deadline
                .saturating_duration_since(Instant::now())
                .min(Duration::from_secs(3)),
        )?;
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        status["broker_running"] = json!(true);
        *self
            .status
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))? = status.clone();
        if request["operation"] == "desktop.sessions" {
            return Ok(json!({"sessions":[status]}));
        }
        if request["payload"]
            .get("session_id")
            .is_some_and(|v| v != child.config.session_id)
        {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        let budget = deadline
            .saturating_duration_since(Instant::now())
            .as_millis()
            .max(1) as u64;
        let context = json!({"owner_id":request["context"]["principal_id"],"device_id":self.device,"operation_id":request["operation_id"],"timeout_ms":budget});
        let mut signal = CancellationSignal::new(&child.path, &request, &cancel)?;
        let mut result = pipe::request(
            &child.config,
            &child.peer,
            json!({"operation":request["operation"],"payload":request["payload"],"context":context}),
            Duration::from_millis(budget),
        )?;
        signal.confirmed = true;
        drop(signal);
        if request["operation"] == "desktop.screenshot" {
            let op = request["operation_id"]
                .as_str()
                .filter(|s| {
                    s.starts_with("op_")
                        && s.len() <= 96
                        && s.bytes()
                            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
                })
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let original = self.spool.join(format!("{op}.png"));
            let descriptor = result
                .as_object_mut()
                .unwrap()
                .remove("capture")
                .ok_or_else(|| RacpError::new("EXECUTION_UNKNOWN"))?;
            self.materialize(
                child,
                &descriptor,
                &original,
                &context,
                deadline,
                &cancel,
                32 * 1024 * 1024,
            )?;
            result["spool_path"] = json!(original);
            result["artifact_media_type"] = json!("image/png");
            result["artifact_id"] = Value::Null;
            if result["preview"].is_object() {
                let path = self.spool.join(format!("{op}.preview.png"));
                let descriptor = result["preview"]
                    .as_object_mut()
                    .unwrap()
                    .remove("capture")
                    .ok_or_else(|| RacpError::new("EXECUTION_UNKNOWN"))?;
                if let Err(e) = self.materialize(
                    child,
                    &descriptor,
                    &path,
                    &context,
                    deadline,
                    &cancel,
                    2 * 1024 * 1024,
                ) {
                    let _ = std::fs::remove_file(&original);
                    return Err(e);
                }
                result["preview"]["spool_path"] = json!(path);
                result["preview"]["artifact_media_type"] = json!("image/png");
                result["preview"]["artifact_id"] = Value::Null;
            }
        }
        Ok(result)
    }
    fn shutdown(&self) -> Result<(), RacpError> {
        if let Some(mut registrar) = self
            .registrar
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
            .take()
        {
            registrar.stop()?;
        }
        let mut guard = self
            .child
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        for child in guard.values_mut() {
            child.stop()?;
        }
        guard.clear();
        Ok(())
    }
}
impl Provider for Desktop {
    fn capabilities(&self) -> Vec<Value> {
        let status = self
            .status
            .lock()
            .map(|s| s.clone())
            .unwrap_or_else(|_| json!({"available":false}));
        vec![
            json!({"name":"desktop","version":"1.0.0","supported":true,"enabled":self.enabled,"healthy":status["broker_running"]==true,"unavailable_reason":status["error_code"],"operations":["clipboard.state","clipboard.read","clipboard.write","desktop.sessions","desktop.monitors","desktop.windows","desktop.foreground","desktop.screenshot","desktop.inspect","desktop.lease_acquire","desktop.lease_renew","desktop.lease_release","desktop.activate","desktop.move","desktop.click","desktop.type","desktop.key","desktop.scroll","desktop.drag","desktop.invoke","desktop.set_value"],"attributes":{"backend":"rust-windows-paired-session-broker","sessions":[status],"input_guardian_available":true,"capture":"visible_rectangle","service_cross_session_registration":true,"service_cross_session_launch":false}}),
        ]
    }
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        let provider = self.clone();
        Box::pin(async move {
            tokio::task::spawn_blocking(move || provider.execute_native(request, cancel))
                .await
                .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?
        })
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        let provider = self.clone();
        Box::pin(async move {
            tokio::task::spawn_blocking(move || provider.shutdown())
                .await
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
        })
    }
}

struct CancellationSignal {
    done: Arc<std::sync::atomic::AtomicBool>,
    join: Option<std::thread::JoinHandle<()>>,
    path: PathBuf,
    confirmed: bool,
}
impl CancellationSignal {
    fn new(
        pair: &std::path::Path,
        r: &Value,
        cancel: &CancellationToken,
    ) -> Result<Self, RacpError> {
        let id = r["operation_id"]
            .as_str()
            .filter(|s| {
                s.starts_with("op_")
                    && s.len() <= 96
                    && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
            })
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let path = pair.with_file_name(format!("cancel-{id}"));
        racp_core::validate_local_path(&path)?;
        let done = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let thread_done = done.clone();
        let token = cancel.clone();
        let output = path.clone();
        let join = std::thread::spawn(move || {
            while !thread_done.load(std::sync::atomic::Ordering::Acquire) {
                if token.is_cancelled() {
                    let _ = racp_core::atomic_write(&output, b"", false);
                    break;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        });
        Ok(Self {
            done,
            join: Some(join),
            path,
            confirmed: false,
        })
    }
}
impl Drop for CancellationSignal {
    fn drop(&mut self) {
        self.done.store(true, std::sync::atomic::Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
        if self.confirmed {
            let _ = std::fs::remove_file(&self.path);
        } else {
            let _ = racp_core::atomic_write(&self.path, b"", false);
        }
    }
}
