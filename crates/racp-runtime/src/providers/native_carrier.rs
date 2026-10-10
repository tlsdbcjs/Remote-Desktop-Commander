//! Process-pinned loopback carrier. Frames never change OS proxies or trust stores.
use super::{
    containment::{CommandSpec, OwnedProcess},
    Provider,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use futures_util::future::BoxFuture;
use racp_contract::{digest, new_id, timestamp, RacpError};
use racp_core::{AgentSettings, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, VecDeque},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{tcp::OwnedWriteHalf, TcpListener, TcpStream},
    sync::{mpsc, Mutex as AsyncMutex, Notify},
};
use tokio_util::sync::CancellationToken;
#[cfg(windows)]
mod windows;
#[derive(Clone)]
pub struct NativeCarrier {
    settings: AgentSettings,
    native_installed: bool,
    boot: String,
    instance: String,
    root: PathBuf,
    state: Arc<AsyncMutex<BTreeMap<String, Arc<Session>>>>,
    connection: Arc<Mutex<Option<(u64, Instant)>>>,
    outgoing: mpsc::Sender<Value>,
    incoming: Arc<Mutex<mpsc::Receiver<Value>>>,
}
struct Session {
    scope: Value,
    fingerprint: String,
    request: Value,
    handle: Mutex<Value>,
    stream: Mutex<Option<String>>,
    expires: Instant,
    connection: Arc<Mutex<Option<(u64, Instant)>>>,
    permissions: Value,
    cancel: CancellationToken,
    channels: AsyncMutex<BTreeMap<u64, Arc<Channel>>>,
    next: Mutex<u64>,
    total: Mutex<u64>,
    max: u64,
    outgoing: mpsc::Sender<Value>,
    #[cfg(windows)]
    target: super::desktop::native_identity::PinnedPeer,
    #[cfg(windows)]
    runtime: Option<windows::Runtime>,
    owned: Mutex<Option<OwnedProcess>>,
    protected: Mutex<Option<crate::identity::ProtectedProcess>>,
    tasks: AsyncMutex<Vec<tokio::task::JoinHandle<()>>>,
    started: Mutex<bool>,
    closing: AsyncMutex<()>,
    root: PathBuf,
}
struct Channel {
    writer: AsyncMutex<OwnedWriteHalf>,
    state: Mutex<Flow>,
    credit: Notify,
}
struct Flow {
    sent: u64,
    received: u64,
    acked: u64,
    pending: VecDeque<u64>,
    out_end: bool,
    in_end: bool,
}
impl Session {
    fn check(&self) -> Result<(), RacpError> {
        if self.cancel.is_cancelled() || Instant::now() >= self.expires {
            return Err(RacpError::new("HANDLE_EXPIRED"));
        }
        let guard = self
            .connection
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let Some((epoch, lease)) = *guard else {
            return Err(RacpError::new("STALE_CONNECTION"));
        };
        if self.scope["connection_epoch"] != epoch || Instant::now() >= lease {
            return Err(RacpError::new("STALE_CONNECTION"));
        }
        racp_core::authorize(&self.permissions, &self.request, false)?;
        #[cfg(windows)]
        self.target.alive()?;
        Ok(())
    }
    fn charge(&self, n: u64) -> Result<(), RacpError> {
        let mut total = self
            .total
            .lock()
            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
        *total = total
            .checked_add(n)
            .filter(|n| *n <= self.max)
            .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
        Ok(())
    }
    fn envelope(&self, kind: &str) -> Result<Value, RacpError> {
        let stream = self
            .stream
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .clone()
            .ok_or_else(|| RacpError::new("PRECONDITION_FAILED"))?;
        Ok(
            json!({"protocol":1,"type":kind,"device_id":self.scope["device_id"],"agent_boot_id":self.scope["agent_boot_id"],"connection_epoch":self.scope["connection_epoch"],"handle_id":self.scope["session_id"],"stream_id":stream}),
        )
    }
    async fn emit(&self, frame: Value) -> Result<(), RacpError> {
        self.check()?;
        let mut value = self.envelope("native_packet")?;
        value["frame"] = frame;
        tokio::select! {_=self.cancel.cancelled()=>Err(RacpError::new("HANDLE_EXPIRED")),v=tokio::time::timeout(Duration::from_secs(5),self.outgoing.send(value))=>v.map_err(|_|RacpError::new("TIMEOUT"))?.map_err(|_|RacpError::new("DEVICE_OFFLINE"))}
    }
    async fn verify(&self, socket: &TcpStream) -> Result<(), RacpError> {
        self.check()?;
        #[cfg(windows)]
        {
            let (pid, birth) = if self.request["operation"] == "native.prepare" {
                let guard = self
                    .owned
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                let child = guard
                    .as_ref()
                    .ok_or_else(|| RacpError::new("PRECONDITION_FAILED"))?;
                (child.pid(), crate::identity::process_created(child.pid())?)
            } else {
                (self.target.identity().pid, self.target.identity().created)
            };
            windows::verify_peer(socket, pid, birth)?;
            Ok(())
        }
        #[cfg(not(windows))]
        {
            let _ = socket;
            Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
        }
    }
    async fn channel(
        self: &Arc<Self>,
        socket: TcpStream,
        id: u64,
        emit_open: bool,
    ) -> Result<(), RacpError> {
        self.verify(&socket).await?;
        if !(1..=16).contains(&id) {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let (mut reader, writer) = socket.into_split();
        let channel = Arc::new(Channel {
            writer: AsyncMutex::new(writer),
            state: Mutex::new(Flow {
                sent: 0,
                received: 0,
                acked: 0,
                pending: VecDeque::new(),
                out_end: false,
                in_end: false,
            }),
            credit: Notify::new(),
        });
        if self
            .channels
            .lock()
            .await
            .insert(id, channel.clone())
            .is_some()
        {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        if emit_open {
            self.emit(
                json!({"type":"native.open","scope_fingerprint":self.fingerprint,"channel_id":id}),
            )
            .await?;
        }
        let this = self.clone();
        let job = tokio::spawn(async move {
            let result=async{
    let mut buffer=[0u8;16384];loop{
     this.check()?;loop{let notified=channel.credit.notified();let full=channel.state.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?.pending.len()>=4;if !full{break;}tokio::select!{_=this.cancel.cancelled()=>return Err(RacpError::new("HANDLE_EXPIRED")),_=notified=>()};this.check()?;}
     let n=tokio::select!{_=this.cancel.cancelled()=>return Err(RacpError::new("HANDLE_EXPIRED")),v=reader.read(&mut buffer)=>v.map_err(|_|RacpError::new("DEVICE_OFFLINE"))?};this.check()?;
     let offset={let mut flow=channel.state.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?;let offset=flow.sent;if n>0{this.charge(n as u64)?;flow.sent=flow.sent.checked_add(n as u64).ok_or_else(||RacpError::new("RESOURCE_EXHAUSTED"))?;let sent=flow.sent;flow.pending.push_back(sent);}else{flow.out_end=true;}offset};
     if n==0{this.emit(json!({"type":"native.end","scope_fingerprint":this.fingerprint,"channel_id":id,"byte_offset":offset})).await?;return Ok::<_,RacpError>(());}
     this.emit(json!({"type":"native.data","scope_fingerprint":this.fingerprint,"channel_id":id,"byte_offset":offset,"data_base64":B64.encode(&buffer[..n])})).await?;
    }
   }.await;
            if result.is_err() {
                this.cancel.cancel();
            }
        });
        self.tasks.lock().await.push(job);
        Ok(())
    }
    async fn listener(self: &Arc<Self>, listener: TcpListener) {
        let this = self.clone();
        let job = tokio::spawn(async move {
            loop {
                let accepted =
                    tokio::select! {_=this.cancel.cancelled()=>break,v=listener.accept()=>v};
                let Ok((socket, _)) = accepted else {
                    this.cancel.cancel();
                    break;
                };
                let id = {
                    let Ok(mut next) = this.next.lock() else {
                        this.cancel.cancel();
                        break;
                    };
                    let id = *next;
                    *next += 1;
                    id
                };
                if this.channel(socket, id, true).await.is_err() {
                    this.cancel.cancel();
                    break;
                }
            }
        });
        self.tasks.lock().await.push(job);
    }
    async fn close(&self) -> Result<(), RacpError> {
        let _closing = self.closing.lock().await;
        if self
            .handle
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?["state"]
            == "CLOSED"
        {
            return Ok(());
        }
        self.cancel.cancel();
        for channel in self.channels.lock().await.values() {
            let _ = channel.writer.lock().await.shutdown().await;
            channel.credit.notify_waiters();
        }
        for job in std::mem::take(&mut *self.tasks.lock().await) {
            job.await.map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        }
        let mut owned = self
            .owned
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        if let Some(child) = owned.as_mut() {
            child.kill_tree()?;
            let deadline = Instant::now() + Duration::from_secs(5);
            while !child.tree_empty()? {
                if Instant::now() >= deadline {
                    return Err(RacpError::new("CLEANUP_FAILED"));
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        *owned = None;
        *self
            .protected
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))? = None;
        {
            let mut handle = self
                .handle
                .lock()
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
            handle["state"] = json!("CLOSED");
            handle["availability"] = json!("unavailable");
        }
        if let Ok(mut value) = self.envelope("native_stopped") {
            value["reason"] = json!("closed");
            let _ = self.outgoing.try_send(value);
        }
        Ok(())
    }
    async fn receive(self: &Arc<Self>, m: Value) -> Result<(), RacpError> {
        self.check()?;
        let kind = m["type"].as_str().unwrap_or("");
        for key in ["device_id", "agent_boot_id", "connection_epoch"] {
            if m[key] != self.scope[key] {
                return Err(RacpError::new("STALE_CONNECTION"));
            }
        }
        if kind == "native_subscribe" {
            if m["principal_id"] != self.scope["principal_id"]
                || m["workspace_id"] != self.scope["workspace_id"]
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            {
                let mut stream = self
                    .stream
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                if stream.is_some() {
                    return Err(RacpError::new("RESOURCE_BUSY"));
                }
                *stream = Some(
                    m["stream_id"]
                        .as_str()
                        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?
                        .into(),
                );
            }
            let mut value = self.envelope("native_opened")?;
            value["scope"] = self.scope.clone();
            tokio::select! {
                _=self.cancel.cancelled()=>return Err(RacpError::new("HANDLE_EXPIRED")),
                sent=tokio::time::timeout(Duration::from_secs(5),self.outgoing.send(value))=>sent.map_err(|_|RacpError::new("TIMEOUT"))?.map_err(|_|RacpError::new("DEVICE_OFFLINE"))?,
            }
            return Ok(());
        }
        if self.envelope(kind)?["stream_id"] != m["stream_id"] {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if kind == "native_unsubscribe" {
            return self.close().await;
        }
        let f = &m["frame"];
        let fields = match f["type"].as_str() {
            Some("native.open") => &["type", "scope_fingerprint", "channel_id"][..],
            Some("native.data") => &[
                "type",
                "scope_fingerprint",
                "channel_id",
                "byte_offset",
                "data_base64",
            ][..],
            Some("native.ack" | "native.end") => {
                &["type", "scope_fingerprint", "channel_id", "byte_offset"][..]
            }
            _ => return Err(RacpError::new("REQUEST_INVALID")),
        };
        if !f
            .as_object()
            .is_some_and(|o| o.len() == fields.len() && fields.iter().all(|k| o.contains_key(*k)))
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        if f["scope_fingerprint"] != self.fingerprint {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let id = f["channel_id"]
            .as_u64()
            .filter(|n| (1..=16).contains(n))
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let kind = f["type"].as_str().unwrap_or("");
        if kind == "native.open" {
            if self.scope["endpoint_role"] != "agent_connector" {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            {
                let mut next = self
                    .next
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                if *next != id {
                    return Err(RacpError::new("INVALID_ARGUMENT"));
                }
                *next += 1;
            }
            let port = self.request["payload"]["local_port"].as_u64().unwrap() as u16;
            let socket = tokio::time::timeout(
                Duration::from_secs(5),
                TcpStream::connect(("127.0.0.1", port)),
            )
            .await
            .map_err(|_| RacpError::new("TIMEOUT"))?
            .map_err(|_| RacpError::new("DEVICE_OFFLINE"))?;
            return self.channel(socket, id, false).await;
        }
        let channel = self
            .channels
            .lock()
            .await
            .get(&id)
            .cloned()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let offset = f["byte_offset"]
            .as_u64()
            .filter(|v| *v <= i64::MAX as u64)
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        if kind == "native.ack" {
            let mut flow = channel
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            if offset <= flow.acked {
                return Ok(());
            }
            if !flow.pending.contains(&offset) {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            while flow.pending.front().is_some_and(|n| *n <= offset) {
                flow.pending.pop_front();
            }
            flow.acked = offset;
            channel.credit.notify_waiters();
            return Ok(());
        }
        let mut writer = channel.writer.lock().await;
        {
            let flow = channel
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            if flow.in_end || flow.received != offset {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
        }
        if kind == "native.end" {
            writer
                .shutdown()
                .await
                .map_err(|_| RacpError::new("DEVICE_OFFLINE"))?;
            channel
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .in_end = true;
            return Ok(());
        }
        if kind != "native.data" {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let encoded = f["data_base64"]
            .as_str()
            .filter(|s| s.len() <= 21848)
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let raw = B64
            .decode(encoded)
            .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
        if raw.is_empty() || raw.len() > 16384 || B64.encode(&raw) != encoded {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.charge(raw.len() as u64)?;
        self.check()?;
        tokio::select! {_=self.cancel.cancelled()=>return Err(RacpError::new("HANDLE_EXPIRED")),v=tokio::time::timeout(Duration::from_secs(5),writer.write_all(&raw))=>v.map_err(|_|RacpError::new("TIMEOUT"))?.map_err(|_|RacpError::new("DEVICE_OFFLINE"))?};
        self.check()?;
        let received = offset
            .checked_add(raw.len() as u64)
            .filter(|v| *v <= i64::MAX as u64)
            .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
        channel
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .received = received;
        self.emit(json!({"type":"native.ack","scope_fingerprint":self.fingerprint,"channel_id":id,"byte_offset":received})).await
    }
}
impl NativeCarrier {
    pub fn new(settings: &AgentSettings, boot: &str) -> Result<Self, RacpError> {
        let root = settings.data_dir.join("native");
        racp_core::private_dir(&root)?;
        let (outgoing, incoming) = mpsc::channel(128);
        #[cfg(windows)]
        let native_installed = windows::Runtime::load(&settings.data_dir).is_ok();
        #[cfg(not(windows))]
        let native_installed = false;
        Ok(Self {
            settings: settings.clone(),
            native_installed,
            boot: boot.into(),
            instance: new_id("native_provider"),
            root,
            state: Arc::new(AsyncMutex::new(BTreeMap::new())),
            connection: Arc::new(Mutex::new(None)),
            outgoing,
            incoming: Arc::new(Mutex::new(incoming)),
        })
    }
    async fn prepare(&self, r: Value, cancel: CancellationToken) -> Result<Value, RacpError> {
        #[cfg(not(windows))]
        {
            let _ = (r, cancel);
            Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
        }
        #[cfg(windows)]
        {
            if cancel.is_cancelled() {
                return Err(RacpError::new("CANCELLED"));
            }
            let op = r["operation"].as_str().unwrap();
            let p = racp_contract::validate_operation(op, r["payload"].clone())?;
            if p["agent_boot_id"] != self.boot {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            super::recipes::validate_target(&p)?;
            let target = super::desktop::native_identity::PinnedPeer::open(
                p["pid"].as_u64().unwrap() as u32,
            )?;
            let mut state = self.state.lock().await;
            state.retain(|_, s| s.handle.lock().is_ok_and(|h| h["state"] != "CLOSED"));
            if state.len() >= 8 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let native = op == "native.prepare";
            let runtime = if native {
                Some(windows::Runtime::load(&self.settings.data_dir)?)
            } else {
                None
            };
            let (epoch, lease) = self
                .connection
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .filter(|(_, lease)| Instant::now() < *lease)
                .ok_or_else(|| RacpError::new("STALE_CONNECTION"))?;
            let principal = r["context"]["principal_id"]
                .as_str()
                .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
            let workspace = r["context"]["workspace_id"].as_str().unwrap_or("default");
            Workspaces::new(&self.settings.workspace, &self.settings.allowed_workspaces)?
                .root(workspace)?;
            let permissions = self
                .settings
                .permissions
                .clone()
                .ok_or_else(|| RacpError::new("PERMISSIONS_INVALID"))?;
            let id = new_id(if native { "native" } else { "proxy" });
            let role = if native {
                "agent_listener"
            } else {
                p["direction"].as_str().unwrap()
            };
            let scope = json!({"session_id":id,"device_id":self.settings.device_id,"agent_boot_id":self.boot,"connection_epoch":epoch,"principal_id":principal,"workspace_id":workspace,"permission_revision":digest(serde_json::to_vec(&permissions)?),"endpoint_role":role});
            let fingerprint = digest(serde_json::to_vec(&scope)?);
            let seconds = p["lease_seconds"].as_u64().unwrap_or(120);
            let created = timestamp();
            let root = self.root.join(&id);
            racp_core::private_dir(&root)?;
            let handle = json!({"id":id,"type":if native{"debugger"}else{"interactive-process"},"device_id":self.settings.device_id,"owner":principal,"agent_boot_id":self.boot,"workspace_id":workspace,"provider_instance_id":self.instance,"resource_revision":"1","created_at":created,"last_access_at":created,"expires_at":(chrono::Utc::now()+chrono::Duration::seconds(seconds as i64)).to_rfc3339(),"state":"ACTIVE","availability":"available","pid":p["pid"],"create_time":p["create_time"],"ownership":"borrowed","backend":if native{"racp-native-cdb"}else{"racp-http-proxy-carrier"}});
            let session = Arc::new(Session {
                scope: scope.clone(),
                fingerprint,
                request: r,
                handle: Mutex::new(handle),
                stream: Mutex::new(None),
                expires: (Instant::now() + Duration::from_secs(seconds))
                    .min(lease + Duration::from_secs(seconds)),
                connection: self.connection.clone(),
                permissions,
                cancel: CancellationToken::new(),
                channels: AsyncMutex::new(BTreeMap::new()),
                next: Mutex::new(1),
                total: Mutex::new(0),
                max: p["max_bytes"].as_u64().unwrap_or(8388608),
                outgoing: self.outgoing.clone(),
                target,
                runtime,
                owned: Mutex::new(None),
                protected: Mutex::new(None),
                tasks: AsyncMutex::new(vec![]),
                started: Mutex::new(false),
                closing: AsyncMutex::new(()),
                root,
            });
            let mut url = Value::Null;
            if !native && role == "agent_listener" {
                let listener = TcpListener::bind(("127.0.0.1", 0))
                    .await
                    .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
                url = json!(format!(
                    "http://127.0.0.1:{}",
                    listener
                        .local_addr()
                        .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?
                        .port()
                ));
                session.listener(listener).await;
            }
            session.check()?;
            state.insert(id, session.clone());
            drop(state);
            let watcher = session.clone();
            tokio::spawn(async move {
                let mut log_bytes = 0usize;
                loop {
                    tokio::select! {_=watcher.cancel.cancelled()=>break,_=tokio::time::sleep(Duration::from_millis(50))=>{if watcher.check().is_err(){break;} let failed = watcher.owned.lock().map(|mut owned| { if let Some(child)=owned.as_mut() { let mut bytes=[0u8;8192]; for pipe in [&mut child.stdout,&mut child.stderr] { for _ in 0..16 { match pipe.read_available(&mut bytes) { Ok(Some(n)) if n>0=>{log_bytes=log_bytes.saturating_add(n); if log_bytes>8*1024*1024{return true;}}, _=>break } } } child.poll().map(|v|v.is_some()).unwrap_or(true) } else { false } }).unwrap_or(true); if failed {break;}}}
                }
                let _ = watcher.close().await;
            });
            Ok(
                json!({"handle":session.handle.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?.clone(),"native_scope":scope,"max_bytes":p["max_bytes"],"lease_seconds":seconds,"proxy_url":url,"symbol_cache":if native{Some(session.root.join("symbols"))}else{None}}),
            )
        }
    }
}
impl Provider for NativeCarrier {
    fn capabilities(&self) -> Vec<Value> {
        let native = self.native_installed;
        vec![
            json!({"name":"proxy","version":"1.0.0","operations":["proxy.prepare","proxy.close"],"installed":cfg!(windows),"supported":cfg!(windows),"enabled":cfg!(windows),"healthy":cfg!(windows),"unavailable_reason":if cfg!(windows){None}else{Some("windows_peer_identity_required")},"attributes":{"backend":"process_bound_tcp_carrier","max_channels":16,"system_proxy_changes":false,"ca_installation":false}}),
            json!({"name":"native","version":"1.0.0","operations":["native.prepare","native.start","native.close"],"installed":native,"supported":cfg!(windows),"enabled":native,"healthy":native,"unavailable_reason":if native{None}else{Some("managed_cdb_runtime_missing")},"attributes":{"opaque_native_commands":true,"broad_execution_grant_required":true,"max_channels":16}}),
        ]
    }
    fn connection(&self, epoch: u64, ttl: Duration) {
        if let Ok(mut connection) = self.connection.lock() {
            *connection = if epoch == 0 {
                None
            } else {
                Some((epoch, Instant::now() + ttl))
            };
        }
    }
    fn native_frames(&self) -> Vec<Value> {
        let Ok(mut incoming) = self.incoming.lock() else {
            return vec![];
        };
        let Ok(sessions) = self.state.try_lock() else {
            return vec![];
        };
        let mut result = vec![];
        for _ in 0..32 {
            match incoming.try_recv() {
                Ok(v) => {
                    let allowed = v["handle_id"]
                        .as_str()
                        .and_then(|id| sessions.get(id))
                        .is_some_and(|session| {
                            let live = session.connection.lock().is_ok_and(|c| {
                                c.is_some_and(|(epoch, lease)| {
                                    v["connection_epoch"] == epoch && Instant::now() < lease
                                })
                            });
                            live && (session.check().is_ok() || v["type"] == "native_stopped")
                        });
                    if allowed {
                        result.push(v);
                    }
                }
                Err(_) => break,
            }
        }
        result
    }
    fn native_message(&self, m: Value) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            let id = m["handle_id"]
                .as_str()
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
            let session = self
                .state
                .lock()
                .await
                .get(id)
                .cloned()
                .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
            let result = session.receive(m).await;
            if result.is_err() {
                session.close().await?;
            }
            result
        })
    }
    fn inventory(&self) -> Vec<Value> {
        let Ok(state) = self.state.try_lock() else {
            return vec![];
        };
        state
            .values()
            .filter_map(|s| s.handle.lock().ok().map(|h| h.clone()))
            .collect()
    }
    fn execute(
        &self,
        r: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        Box::pin(async move {
            let op = r["operation"].as_str().unwrap_or("");
            let result = if matches!(op, "native.prepare" | "proxy.prepare") {
                self.prepare(r, cancel).await?
            } else {
                let id = r["payload"]["handle_id"]
                    .as_str()
                    .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
                let session = self
                    .state
                    .lock()
                    .await
                    .get(id)
                    .cloned()
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
                if session.scope["principal_id"] != r["context"]["principal_id"]
                    || session.scope["workspace_id"] != r["context"]["workspace_id"]
                {
                    return Err(RacpError::new("PERMISSION_DENIED"));
                }
                if op == "native.start" {
                    session.check()?;
                    if session
                        .stream
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                        .is_none()
                    {
                        return Err(RacpError::new("PRECONDITION_FAILED"));
                    }
                    {
                        let mut started = session
                            .started
                            .lock()
                            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                        if *started {
                            return Err(RacpError::new("PRECONDITION_FAILED"));
                        }
                        *started = true;
                    }
                    #[cfg(windows)]
                    {
                        let listener = TcpListener::bind(("127.0.0.1", 0))
                            .await
                            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
                        let port = listener
                            .local_addr()
                            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?
                            .port();
                        let runtime = session
                            .runtime
                            .as_ref()
                            .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
                        let argv =
                            runtime.command(session.target.identity().pid, port, &session.root)?;
                        let cwd = Workspaces::new(&session.root, &[])?
                            .directory("default", &session.root)?;
                        let mut environment = BTreeMap::new();
                        for name in ["SystemRoot", "WINDIR", "TEMP", "TMP"] {
                            if let Ok(value) = std::env::var(name) {
                                environment.insert(name.into(), value);
                            }
                        }
                        let child = OwnedProcess::spawn(CommandSpec {
                            argv,
                            environment,
                            cwd,
                        })?;
                        *session
                            .protected
                            .lock()
                            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))? =
                            Some(crate::identity::ProtectedProcess::register(child.pid())?);
                        *session
                            .owned
                            .lock()
                            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))? = Some(child);
                        session.listener(listener).await;
                    }
                    #[cfg(not(windows))]
                    return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
                } else {
                    session.close().await?;
                }
                json!({"handle":session.handle.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?.clone()})
            };
            Ok(json!({"state":"SUCCEEDED","result":result,"error":null}))
        })
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            let sessions: Vec<_> = self.state.lock().await.values().cloned().collect();
            for session in sessions {
                session.close().await?;
            }
            Ok(())
        })
    }
}

pub fn provision_native(args: &[String], state: &std::path::Path) -> Result<Value, RacpError> {
    #[cfg(windows)]
    {
        windows::provision(args, state)
    }
    #[cfg(not(windows))]
    {
        let _ = (args, state);
        Err(RacpError::new("OPERATION_NOT_SUPPORTED"))
    }
}
