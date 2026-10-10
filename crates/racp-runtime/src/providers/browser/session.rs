use super::{cdp::Cdp, BrowserConfig};
use crate::providers::{
    containment::{self, CommandSpec, OwnedProcess},
    Provider,
};
use futures_util::future::BoxFuture;
use racp_contract::{new_id, timestamp, RacpError};
use racp_core::{error_value, private_dir, AgentSettings, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;

pub(super) struct Frame {
    pub id: String,
    pub native: String,
    pub parent: Option<String>,
    pub session: String,
    pub context: Option<String>,
    pub url: String,
    pub name: String,
    pub active: bool,
}
pub(super) struct Page {
    pub browser_context: String,
    pub target: String,
    pub session: String,
    pub handle: Value,
    pub revision: u64,
    pub main: String,
    pub frames: BTreeMap<String, Frame>,
    pub observation: Option<(String, String, u64)>,
    pub refs: BTreeMap<String, String>,
    pub loading: bool,
    pub blocked: bool,
}
fn detach(page: &mut Page, session: &str, native: &str, swapping: bool) -> bool {
    let mut detached = vec![native.to_owned()];
    for _ in 0..128 {
        let children = page
            .frames
            .values()
            .filter(|f| {
                f.active
                    && f.parent.as_ref().is_some_and(|p| detached.contains(p))
                    && !detached.contains(&f.native)
            })
            .map(|f| f.native.clone())
            .collect::<Vec<_>>();
        if children.is_empty() {
            break;
        }
        detached.extend(children);
    }
    let mut changed = false;
    for frame in page.frames.values_mut().filter(|f| {
        (page.session == session || f.session == session) && detached.contains(&f.native)
    }) {
        if swapping && frame.session != session {
            // The replacement OOPIF can be ready before the old parent reports its
            // swap. That notification must not erase the replacement's context.
            continue;
        }
        if !swapping {
            frame.active = false;
        }
        frame.context = None;
        changed = true;
    }
    changed
}

pub(super) struct State {
    pub history: Vec<Value>,
    pub uploads: usize,
    pub download: Option<super::files::Download>,
    pub handle: Value,
    pub pages: BTreeMap<String, Page>,
    pub expires: Instant,
}
pub(super) struct Session {
    pub(super) events: Arc<Mutex<super::BrowserOutbox>>,
    pub(super) siblings: std::sync::Weak<Mutex<BTreeMap<String, Arc<Session>>>>,
    pub opening_operation: String,
    pub backend_version: String,
    pub remote: Option<String>,
    pub borrowed: bool,
    pub allow_termination: bool,
    pub selected: Vec<String>,
    pub cdp: Cdp,
    pub context: String,
    pub state: Mutex<State>,
    pub process: Mutex<Option<OwnedProcess>>,
    pub protected: Mutex<Option<crate::identity::ProtectedProcess>>,
    pub proxy: super::PolicyProxy,
    pub profile: PathBuf,
    pub width: u64,
    pub height: u64,
    pub serial: tokio::sync::Mutex<()>,
    pub(super) closing: tokio::sync::Mutex<()>,
    pub notify: tokio::sync::Notify,
    pub stop: CancellationToken,
    pub worker: tokio::sync::Mutex<Option<tokio::task::JoinHandle<()>>>,
}
#[derive(Clone)]
pub struct Browser {
    pub(super) ready: Arc<std::sync::atomic::AtomicBool>,
    pub(super) outbox: Arc<Mutex<super::BrowserOutbox>>,
    pub(super) cursor: crate::providers::cursor::Cursor,
    pub(super) opening: Arc<tokio::sync::Mutex<()>>,
    pub(super) config: BrowserConfig,
    pub(super) sessions: Arc<Mutex<BTreeMap<String, Arc<Session>>>>,
    pub(super) root: PathBuf,
    pub(super) spool: PathBuf,
    pub(super) boot: String,
    pub(super) device: String,
    pub(super) instance: String,
    pub(super) guards: Workspaces,
}
impl Browser {
    pub fn new(
        settings: &AgentSettings,
        boot: &str,
        config: BrowserConfig,
    ) -> Result<Self, RacpError> {
        for origin in &config.allow_origins {
            let url = url::Url::parse(origin).map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
            if !config.allowed(origin)
                || url.path() != "/"
                || url.query().is_some()
                || url.fragment().is_some()
            {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
        }
        let root = settings.data_dir.join("browser");
        private_dir(&root)?;
        let spool = settings.data_dir.join("spool");
        private_dir(&spool)?;
        let markers = super::recovery::markers(&root)?;
        let instance = new_id("provider");
        let this = Self {
            ready: Arc::new(std::sync::atomic::AtomicBool::new(markers.is_empty())),
            outbox: Arc::new(Mutex::new(super::BrowserOutbox::new(&instance, 128))),
            cursor: Default::default(),
            opening: Arc::default(),
            config,
            sessions: Arc::default(),
            root,
            spool,
            boot: boot.into(),
            device: settings.device_id.clone(),
            instance,
            guards: Workspaces::new(&settings.workspace, &settings.allowed_workspaces)?,
        };
        this.recover(markers);
        let weak = Arc::downgrade(&this.sessions);
        let quota_root = this.root.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_millis(100)).await;
                let Some(sessions) = weak.upgrade() else {
                    break;
                };
                let snapshot = sessions
                    .lock()
                    .map(|s| s.values().cloned().collect::<Vec<_>>())
                    .unwrap_or_default();
                let quota = racp_core::browser_temporary_usage(&quota_root);
                if let Err(ref error) = quota {
                    eprintln!("browser quota observation failed code={}", error.code.0);
                }
                let quota_exhausted = quota.map_or(true, |used| used > 10 * 1024 * 1024 * 1024);
                for session in snapshot {
                    let expired = session
                        .state
                        .lock()
                        .is_ok_and(|s| s.expires <= Instant::now());
                    if let Ok(mut owned) = session.process.lock() {
                        if let Some(owned) = owned.as_mut() {
                            let mut data = [0; 8192];
                            for pipe in [&mut owned.stdout, &mut owned.stderr] {
                                for _ in 0..8 {
                                    if !matches!(pipe.read_available(&mut data),Ok(Some(n)) if n>0)
                                    {
                                        break;
                                    }
                                }
                            }
                        }
                    }
                    let active = session
                        .state
                        .lock()
                        .is_ok_and(|s| s.handle["state"] == "ACTIVE");
                    if active && (expired || quota_exhausted || session.cdp.is_closed()) {
                        let _ = session.close().await;
                    }
                }
            }
        });
        Ok(this)
    }
    pub(super) fn get(&self, request: &Value) -> Result<Arc<Session>, RacpError> {
        if request["payload"]["agent_boot_id"]
            .as_str()
            .is_some_and(|boot| boot != self.boot)
        {
            return Err(RacpError::new("HANDLE_EXPIRED"));
        }
        let id = request["payload"]["browser_id"].as_str().unwrap_or("");
        let session = self
            .sessions
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .get(id)
            .cloned()
            .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
        {
            let state = session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            if state.handle["owner"] != request["context"]["principal_id"]
                || state.handle["workspace_id"] != request["context"]["workspace_id"]
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
        }
        Ok(session)
    }
    pub(super) async fn open(
        &self,
        request: &Value,
        cancel: &CancellationToken,
        effect: &std::sync::atomic::AtomicBool,
    ) -> Result<Value, RacpError> {
        let _opening = tokio::select! { _=cancel.cancelled()=>return Err(RacpError::new("CANCELLED")), opening=self.opening.lock()=>opening };
        let workspace = request["context"]["workspace_id"]
            .as_str()
            .unwrap_or("default");
        self.guards.root(workspace)?;
        let id = new_id("browser");
        {
            let mut sessions = self
                .sessions
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            sessions.retain(|_, s| {
                s.state
                    .lock()
                    .is_ok_and(|s| s.expires > Instant::now() || s.handle["state"] != "CLOSED")
            });
            if sessions.len() >= 12
                || sessions
                    .values()
                    .filter(|s| {
                        s.state.lock().is_ok_and(|s| {
                            matches!(s.handle["state"].as_str(), Some("ACTIVE" | "CREATING"))
                        })
                    })
                    .count()
                    >= 4
            {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
        }
        let executable = self
            .config
            .executable
            .as_ref()
            .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
        racp_core::validate_local_path(executable)?;
        if !executable.is_file() {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let profile = self.root.join(&id);
        private_dir(&profile)?;
        let guards = Workspaces::new(&profile, &[])?;
        let cwd = guards.directory("default", &profile)?;
        let mut argv = vec![
            executable.to_string_lossy().into_owned(),
            format!("--user-data-dir={}", profile.display()),
            "--remote-debugging-port=0".into(),
            "--remote-debugging-address=127.0.0.1".into(),
            "--no-first-run".into(),
            "--no-default-browser-check".into(),
            "--no-startup-window".into(),
            "--disable-background-networking".into(),
            "--disable-component-update".into(),
            "--disable-sync".into(),
            "--disable-default-apps".into(),
            "--disable-extensions".into(),
            "--disable-popup-blocking".into(),
        ];
        if request["payload"]["headless"] != false {
            argv.push("--headless=new".into());
        }
        effect.store(true, std::sync::atomic::Ordering::SeqCst);
        let mut process = OwnedProcess::spawn(CommandSpec {
            argv,
            environment: containment::environment(&json!({}))?,
            cwd,
        })?;
        let protected = crate::identity::ProtectedProcess::register(process.pid())?;
        if let Err(error) =
            super::recovery::persist_process(&profile, &self.device, &self.boot, &process)
        {
            drop(process);
            let _ = std::fs::remove_dir_all(&profile);
            return Err(error);
        }
        let connection = async {
            let deadline = Instant::now() + Duration::from_secs(10);
            loop {
                if cancel.is_cancelled() {
                    return Err(RacpError::new("CANCELLED"));
                }
                if process.poll()?.is_some() {
                    return Err(RacpError::new("BROWSER_UNAVAILABLE"));
                }
                if let Ok(port) =
                    racp_core::read_bounded(&profile.join("DevToolsActivePort"), 4096, false)
                {
                    let port =
                        String::from_utf8(port).map_err(|_| RacpError::new("BROWSER_ERROR"))?;
                    let mut lines = port.lines();
                    let port = lines
                        .next()
                        .and_then(|p| p.parse::<u16>().ok())
                        .filter(|p| *p != 0)
                        .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?;
                    let path = lines
                        .next()
                        .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?;
                    return Cdp::connect(&format!("ws://127.0.0.1:{port}{path}")).await;
                }
                if Instant::now() >= deadline {
                    return Err(RacpError::new("TIMEOUT"));
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        }
        .await;
        let cdp = match connection {
            Ok(cdp) => cdp,
            Err(e) => {
                drop(process);
                let _ = std::fs::remove_dir_all(&profile);
                return Err(e);
            }
        };
        let backend_version = match cdp
            .call("Browser.getVersion", json!({}), None, cancel)
            .await
        {
            Ok(version) => {
                super::operations::bounded(version["product"].as_str().unwrap_or(""), 256)
            }
            Err(error) => {
                cdp.close().await;
                drop(process);
                let _ = std::fs::remove_dir_all(&profile);
                return Err(error);
            }
        };
        let proxy = match super::PolicyProxy::bind(self.config.clone()).await {
            Ok(proxy) => proxy,
            Err(error) => {
                cdp.close().await;
                drop(process);
                let _ = std::fs::remove_dir_all(&profile);
                return Err(error);
            }
        };
        let context = match cdp
            .call(
                "Target.createBrowserContext",
                json!({"disposeOnDetach":true,"proxyServer":format!("http://{}",proxy.address()),"proxyBypassList":"<-loopback>"}),
                None,
                cancel,
            )
            .await
        {
            Ok(value) => value["browserContextId"].as_str().unwrap_or("").to_owned(),
            Err(e) => {
                cdp.close().await;
                drop(process);
                let _ = std::fs::remove_dir_all(&profile);
                return Err(e);
            }
        };
        let now = timestamp();
        let handle = json!({"id":id,"type":"browser","device_id":self.device,"owner":request["context"]["principal_id"],"agent_boot_id":self.boot,"workspace_id":workspace,"provider_instance_id":self.instance,"resource_revision":"1","created_at":now,"last_access_at":now,"expires_at":(chrono::Utc::now()+chrono::Duration::hours(1)).to_rfc3339_opts(chrono::SecondsFormat::Micros,true),"state":"CREATING","availability":"unavailable","ownership":"racp_owned"});
        let session = Arc::new(Session {
            events: self.outbox.clone(),
            siblings: Arc::downgrade(&self.sessions),
            opening_operation: request["operation_id"].as_str().unwrap_or("").into(),
            backend_version,
            remote: None,
            borrowed: false,
            allow_termination: true,
            selected: vec![],
            cdp,
            context,
            state: Mutex::new(State {
                history: vec![],
                uploads: 0,
                download: None,
                handle,
                pages: BTreeMap::new(),
                expires: Instant::now() + Duration::from_secs(3600),
            }),
            process: Mutex::new(Some(process)),
            protected: Mutex::new(Some(protected)),
            proxy,
            profile,
            width: request["payload"]["width"].as_u64().unwrap_or(1280),
            height: request["payload"]["height"].as_u64().unwrap_or(720),
            serial: tokio::sync::Mutex::new(()),
            closing: tokio::sync::Mutex::new(()),
            notify: tokio::sync::Notify::new(),
            stop: CancellationToken::new(),
            worker: tokio::sync::Mutex::new(None),
        });
        self.sessions
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .insert(id.clone(), session.clone());
        session.emit("inventory", "pending");
        session.start(self.config.clone()).await;
        let result = async {
            session.watch_downloads(cancel).await?;
            session
                .cdp
                .call(
                    "Target.setDiscoverTargets",
                    json!({"discover":true}),
                    None,
                    cancel,
                )
                .await?;
            session
                .cdp
                .call(
                    "Target.setAutoAttach",
                    json!({"autoAttach":true,"waitForDebuggerOnStart":true,"flatten":true}),
                    None,
                    cancel,
                )
                .await?;
            session.new_page(cancel).await?;
            session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .handle["state"] = json!("ACTIVE");
            session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .handle["availability"] = json!("available");
            session.emit("inventory", "active");
            Ok(session.opened())
        }
        .await;
        if result.is_err() {
            session.close().await?;
        }
        result
    }
}
impl Browser {
    pub(super) fn capabilities_value(&self) -> Value {
        self.capabilities()[0].clone()
    }
}
impl Session {
    async fn configure_network(
        &self,
        session: &str,
        config: &BrowserConfig,
    ) -> Result<(), RacpError> {
        self.cdp
            .call(
                "Network.enable",
                json!({"maxPostDataSize":0}),
                Some(session),
                &self.stop,
            )
            .await?;
        self.network_policy(session, config).await?;
        self.cdp
            .call(
                "Fetch.enable",
                json!({"patterns":[{"urlPattern":"*"}]}),
                Some(session),
                &self.stop,
            )
            .await?;
        Ok(())
    }
    pub(super) async fn network_policy(
        &self,
        session: &str,
        config: &BrowserConfig,
    ) -> Result<(), RacpError> {
        // Pinned Chromium 153 exposes ordered native URLPattern rules, including
        // allow rules. WebSockets additionally require the protected constructor guard
        // installed before page/worker scripts; CDP URL blocking does not fence them.
        let mut patterns = vec![];
        if config.allow_origins.is_empty() {
            for scheme in ["http", "https", "ws", "wss"] {
                patterns.push(json!({"urlPattern":format!("{scheme}://*:*/*"),"block":false}));
            }
        } else {
            for origin in &config.allow_origins {
                let url =
                    url::Url::parse(origin).map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
                patterns.push(json!({"urlPattern":format!("{}/*",url.origin().ascii_serialization()),"block":false}));
                let mut socket = url.clone();
                let scheme = if url.scheme() == "https" { "wss" } else { "ws" };
                socket
                    .set_scheme(scheme)
                    .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
                patterns.push(json!({"urlPattern":format!("{}/*",socket.origin().ascii_serialization()),"block":false}));
            }
        }
        patterns.push(json!({"urlPattern":"*://*:*/*","block":true}));
        patterns.push(json!({"urlPattern":"file:///*","block":true}));
        self.cdp
            .call(
                "Network.setBlockedURLs",
                json!({"urlPatterns":patterns}),
                Some(session),
                &self.stop,
            )
            .await?;

        Ok(())
    }
    pub(super) fn emit(&self, kind: &str, status: &str) {
        if let Ok(mut state) = self.state.lock() {
            state.history.push(json!({"type":kind,"state":status}));
            if state.history.len() > 32 {
                state.history.remove(0);
            }
        }
        let Some(siblings) = self.siblings.upgrade() else {
            return;
        };
        let inventory = siblings
            .lock()
            .map(|sessions| {
                sessions
                    .values()
                    .flat_map(|session| {
                        session
                            .state
                            .lock()
                            .map(|s| {
                                std::iter::once(s.handle.clone())
                                    .chain(s.pages.values().map(|p| p.handle.clone()))
                                    .collect::<Vec<_>>()
                            })
                            .unwrap_or_default()
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        if let Ok(mut events) = self.events.lock() {
            events.push(json!({"browser_id":self.handle()["id"],"kind":kind,"state":status,"handles":inventory}),inventory.clone());
        }
    }
    pub fn handle(&self) -> Value {
        self.state
            .lock()
            .map(|s| s.handle.clone())
            .unwrap_or(Value::Null)
    }
    pub(super) fn opened(&self) -> Value {
        json!({"browser_id":self.handle()["id"],"agent_boot_id":self.handle()["agent_boot_id"],"handle":self.handle(),"pages":self.pages(),"backend_version":self.backend_version,"ownership":if self.borrowed {"borrowed"}else{"racp_owned"},"profile":if self.borrowed{"existing_explicit"}else{"isolated_ephemeral"},"sandbox":if self.remote.is_some(){json!("external_unverified")}else{json!(true)}})
    }
    pub fn pages(&self) -> Vec<Value> {
        self.state.lock().map(|s|s.pages.iter().map(|(id,p)|json!({"page_id":id,"navigation_revision":p.revision.to_string(),"closed":p.handle["state"]=="CLOSED","ownership":p.handle["ownership"],"cdp_target_id":p.target})).collect()).unwrap_or_default()
    }
    pub async fn new_page(&self, cancel: &CancellationToken) -> Result<Value, RacpError> {
        if self
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .pages
            .len()
            >= 8
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let target = self
            .cdp
            .call(
                "Target.createTarget",
                json!({"url":"about:blank","browserContextId":self.context}),
                None,
                cancel,
            )
            .await?;
        let target = target["targetId"].as_str().unwrap_or("");
        loop {
            let notified = self.notify.notified();
            if let Some(id) = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .pages
                .iter()
                .find(|(_, p)| {
                    p.target == target && p.frames.get(&p.main).is_some_and(|f| f.context.is_some())
                })
                .map(|(id, _)| id.clone())
            {
                return Ok(json!({"page_id":id}));
            }
            tokio::select! { _=cancel.cancelled()=>return Err(RacpError::new("CANCELLED")),_=self.stop.cancelled()=>return Err(RacpError::new("BROWSER_ERROR")),_=notified=>{} }
        }
    }
    pub async fn close(&self) -> Result<Value, RacpError> {
        let _closing = self.closing.lock().await;
        {
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
            if state.handle["state"] == "CLOSED" {
                return Ok(
                    json!({"browser_id":state.handle["id"],"cleanup_status":"complete","state":"CLOSED","resource_disposition":if self.remote.is_some(){"external_browser_preserved"}else{"owned_browser_terminated"},"handle":state.handle}),
                );
            }
            state.handle["state"] = json!("CLOSING");
            state.handle["availability"] = json!("unavailable");
        }
        // Stop target configuration before disposing contexts. A late attachment failure
        // during disposal must not close the transport used by scoped cleanup.
        self.stop.cancel();
        if let Some(worker) = self.worker.lock().await.take() {
            let _ = worker.await;
        }
        if self.remote.is_some() {
            self.cleanup_remote().await?;
        }
        self.proxy.close().await;
        self.cdp.close().await;
        {
            let mut owned = self
                .process
                .lock()
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
            if let Some(process) = owned.as_mut() {
                process.kill_tree()?;
                let deadline = Instant::now() + Duration::from_secs(5);
                loop {
                    if process.poll()?.is_some() && process.tree_empty()? {
                        break;
                    }
                    if Instant::now() >= deadline {
                        return Err(RacpError::new("CLEANUP_FAILED"));
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                owned.take();
                self.protected
                    .lock()
                    .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
                    .take();
            }
        }
        // Only our uniquely created profile is removed, after the owned process tree exits.
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if !self.profile.exists() {
                break;
            }
            match std::fs::remove_dir_all(&self.profile) {
                Ok(()) => break,
                Err(_) if Instant::now() >= deadline => {
                    return Err(RacpError::new("CLEANUP_FAILED"))
                }
                Err(_) => tokio::time::sleep(Duration::from_millis(20)).await,
            }
        }
        let mut state = self
            .state
            .lock()
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        state.handle["state"] = json!("CLOSED");
        state.handle["availability"] = json!("unavailable");
        for p in state.pages.values_mut() {
            p.handle["state"] = json!("CLOSED");
            p.handle["availability"] = json!("unavailable");
        }
        let result = json!({"browser_id":state.handle["id"],"cleanup_status":"complete","state":"CLOSED","resource_disposition":if self.remote.is_some(){"external_browser_preserved"}else{"owned_browser_terminated"},"handle":state.handle});
        drop(state);
        self.emit("inventory", "closed");
        Ok(result)
    }
    pub(super) async fn start(self: &Arc<Self>, config: BrowserConfig) {
        let mut events = self.cdp.subscribe();
        let weak = Arc::downgrade(self);
        let stop = self.stop.clone();
        *self.worker.lock().await = Some(tokio::spawn(async move {
            loop {
                let event =
                    tokio::select! { _=stop.cancelled()=>break, event=events.recv()=>event };
                let Some(session) = weak.upgrade() else { break };
                let Ok(event) = event else {
                    session.stop.cancel();
                    if !session
                        .state
                        .lock()
                        .is_ok_and(|s| s.handle["state"] == "CLOSING")
                    {
                        session.cdp.close().await;
                    }
                    break;
                };
                let method = event["method"].as_str().unwrap_or("").to_owned();
                let event_target = event["params"]["targetInfo"]["targetId"].clone();
                let result = session.event(event, &config).await;
                if let Err(error) = result {
                    if method == "Target.attachedToTarget"
                        && error.code.0 == "EXECUTION_FAILED"
                        && !session.stop.is_cancelled()
                    {
                        let target = event_target.as_str().unwrap_or("");
                        if let Ok(current) = session
                            .cdp
                            .call("Target.getTargets", json!({}), None, &session.stop)
                            .await
                        {
                            if current["targetInfos"].as_array().is_some_and(|targets| {
                                !targets.iter().any(|value| value["targetId"] == target)
                            }) {
                                if let Ok(mut state) = session.state.lock() {
                                    for page in state
                                        .pages
                                        .values_mut()
                                        .filter(|page| page.target == target)
                                    {
                                        page.handle["state"] = json!("CLOSED");
                                        page.handle["availability"] = json!("unavailable");
                                        for frame in page.frames.values_mut() {
                                            frame.active = false;
                                            frame.context = None;
                                        }
                                    }
                                }
                                session.notify.notify_waiters();
                                continue;
                            }
                        }
                    }
                    eprintln!("browser event failed method={method} code={} remote={} borrowed={} state={}", error.code.0, session.remote.is_some(), session.borrowed, session.handle()["state"]);
                    session.stop.cancel();
                    if !session
                        .state
                        .lock()
                        .is_ok_and(|s| s.handle["state"] == "CLOSING")
                    {
                        session.cdp.close().await;
                    }
                    break;
                }
                session.notify.notify_waiters();
            }
        }));
    }
    async fn event(&self, event: Value, config: &BrowserConfig) -> Result<(), RacpError> {
        let method = event["method"].as_str().unwrap_or("");
        let params = &event["params"];
        let session = event["sessionId"].as_str().unwrap_or("");
        if matches!(
            method,
            "Browser.downloadWillBegin" | "Browser.downloadProgress"
        ) {
            return self.download_event(method, params, config).await;
        }
        if method == "Target.targetCreated"
            && self.remote.is_some()
            && params["targetInfo"]["type"] == "page"
            && params["targetInfo"]["browserContextId"] == self.context
        {
            let known = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .pages
                .values()
                .any(|p| p.target == params["targetInfo"]["targetId"]);
            if !known {
                self.cdp
                    .call(
                        "Target.attachToTarget",
                        json!({"targetId":params["targetInfo"]["targetId"],"flatten":true}),
                        None,
                        &self.stop,
                    )
                    .await?;
            }
        } else if method == "Target.attachedToTarget" {
            let target = &params["targetInfo"];
            let child = params["sessionId"].as_str().unwrap_or("");
            let accepted = {
                let state = self
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                target["browserContextId"] == self.context && !self.context.is_empty()
                    || self.borrowed && self.selected.iter().any(|id| target["targetId"] == *id)
                    || !session.is_empty()
                        && state.pages.values().any(|page| {
                            page.session == session
                                || page
                                    .frames
                                    .values()
                                    .any(|frame| frame.active && frame.session == session)
                        })
            };
            if !accepted
                || !matches!(
                    target["type"].as_str(),
                    Some("page" | "iframe" | "worker" | "shared_worker" | "service_worker")
                )
            {
                // Browser-level discovery includes unrelated and transient Chrome workers.
                // Never configure or terminate targets outside our context/selected page subtree.
                if params["waitingForDebugger"] == true {
                    let _ = self
                        .cdp
                        .call(
                            "Runtime.runIfWaitingForDebugger",
                            json!({}),
                            Some(child),
                            &self.stop,
                        )
                        .await;
                }
                let _ = self
                    .cdp
                    .call(
                        "Target.detachFromTarget",
                        json!({"sessionId":child}),
                        None,
                        &self.stop,
                    )
                    .await;
                return Ok(());
            }
            if target["type"] == "page" {
                let id = new_id("page");
                let full = self
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                    .pages
                    .len()
                    >= 8;
                if full {
                    self.cdp
                        .call(
                            "Target.closeTarget",
                            json!({"targetId":target["targetId"]}),
                            None,
                            &self.stop,
                        )
                        .await?;
                    return Ok(());
                }
                {
                    let mut state = self
                        .state
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                    let mut handle = state.handle.clone();
                    handle["id"] = json!(id);
                    handle["ownership"] = json!(if self.borrowed
                        && self.selected.iter().any(|t| target["targetId"] == *t)
                    {
                        "borrowed"
                    } else {
                        "racp_owned"
                    });
                    handle["type"] = json!("browser-page");
                    handle["state"] = json!("ACTIVE");
                    handle["availability"] = json!("available");
                    state.pages.insert(
                        id,
                        Page {
                            browser_context: target["browserContextId"]
                                .as_str()
                                .unwrap_or("")
                                .into(),
                            target: target["targetId"].as_str().unwrap_or("").into(),
                            session: child.into(),
                            handle,
                            revision: 1,
                            main: String::new(),
                            frames: BTreeMap::new(),
                            observation: None,
                            refs: BTreeMap::new(),
                            loading: false,
                            blocked: false,
                        },
                    );
                }
                self.cdp
                    .call("Page.enable", json!({}), Some(child), &self.stop)
                    .await?;
                self.cdp
                    .call(
                        "Page.addScriptToEvaluateOnNewDocument",
                        json!({"source":config.script(),"runImmediately":true}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
                let tree = self
                    .cdp
                    .call("Page.getFrameTree", json!({}), Some(child), &self.stop)
                    .await?;
                self.frame(child, &tree["frameTree"]["frame"])?;
                self.cdp
                    .call("Runtime.enable", json!({}), Some(child), &self.stop)
                    .await?;
                self.configure_network(child, config).await?;
                if !self.borrowed || !self.selected.iter().any(|t| target["targetId"] == *t) {
                    self.cdp.call("Emulation.setDeviceMetricsOverride",json!({"width":self.width,"height":self.height,"deviceScaleFactor":1,"mobile":false}),Some(child),&self.stop).await?;
                }
                self.cdp
                    .call(
                        "Target.setAutoAttach",
                        json!({"autoAttach":true,"waitForDebuggerOnStart":true,"flatten":true}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
                self.cdp
                    .call(
                        "Runtime.runIfWaitingForDebugger",
                        json!({}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
            } else if target["type"] == "iframe" {
                let target_id = target["targetId"].as_str().unwrap_or("");
                let found = {
                    let mut state = self
                        .state
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                    let frame = state
                        .pages
                        .values_mut()
                        .flat_map(|p| p.frames.values_mut())
                        .find(|f| f.active && f.native == target_id);
                    if let Some(frame) = frame {
                        frame.session = child.into();
                        frame.context = None;
                        true
                    } else {
                        false
                    }
                };
                if !found {
                    return Err(RacpError::new("BROWSER_ERROR"));
                }
                self.cdp
                    .call("Page.enable", json!({}), Some(child), &self.stop)
                    .await?;
                self.cdp
                    .call(
                        "Page.addScriptToEvaluateOnNewDocument",
                        json!({"source":config.script(),"runImmediately":true}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
                let tree = self
                    .cdp
                    .call("Page.getFrameTree", json!({}), Some(child), &self.stop)
                    .await?;
                self.frame(child, &tree["frameTree"]["frame"])?;
                self.cdp
                    .call("Runtime.enable", json!({}), Some(child), &self.stop)
                    .await?;
                self.configure_network(child, config).await?;
                self.cdp
                    .call(
                        "Target.setAutoAttach",
                        json!({"autoAttach":true,"waitForDebuggerOnStart":true,"flatten":true}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
                self.cdp
                    .call(
                        "Runtime.runIfWaitingForDebugger",
                        json!({}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
            } else if target["type"] == "service_worker" {
                self.cdp
                    .call(
                        "Target.closeTarget",
                        json!({"targetId":target["targetId"]}),
                        None,
                        &self.stop,
                    )
                    .await?;
            } else {
                // Chromium's dedicated/shared worker sessions expose Network but not Fetch.
                // Their HTTP traffic also passes the context proxy; sockets are guarded
                // before worker scripts resume.
                self.cdp
                    .call(
                        "Network.enable",
                        json!({"maxPostDataSize":0}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
                self.network_policy(child, config).await?;
                self.cdp
                    .call(
                        "Runtime.evaluate",
                        json!({"expression":config.script()}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
                self.cdp
                    .call(
                        "Runtime.runIfWaitingForDebugger",
                        json!({}),
                        Some(child),
                        &self.stop,
                    )
                    .await?;
            }
        } else if method == "Fetch.requestPaused" {
            let url = params["request"]["url"].as_str().unwrap_or("");
            let armed_blob = url
                .strip_prefix("blob:")
                .is_some_and(|inner| config.allowed(inner))
                && {
                    let state = self
                        .state
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                    state.download.as_ref().is_some_and(|download| {
                        state.pages.get(download.page()).is_some_and(|page| {
                            page.frames
                                .values()
                                .any(|frame| frame.active && frame.native == params["frameId"])
                        })
                    })
                };
            let allowed = config.allowed(url) || armed_blob;
            if !allowed && params["resourceType"] == "Document" {
                let mut state = self
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                for page in state.pages.values_mut().filter(|p| p.session == session) {
                    page.blocked = true;
                }
            }
            let method = if allowed {
                "Fetch.continueRequest"
            } else {
                "Fetch.failRequest"
            };
            let mut args = json!({"requestId":params["requestId"]});
            if !allowed {
                args["errorReason"] = json!("BlockedByClient");
            }
            self.cdp
                .call(method, args, Some(session), &self.stop)
                .await?;
        } else if method == "Page.javascriptDialogOpening" {
            self.cdp
                .call(
                    "Page.handleJavaScriptDialog",
                    json!({"accept":false}),
                    Some(session),
                    &self.stop,
                )
                .await?;
        } else if method == "Page.frameNavigated" {
            self.frame(session, &params["frame"])?;
        } else if method == "Page.frameAttached" {
            self.frame(
                session,
                &json!({"id":params["frameId"],"parentId":params["parentFrameId"]}),
            )?;
        } else if method == "Page.frameDetached" {
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            for page in state.pages.values_mut() {
                let changed = detach(
                    page,
                    session,
                    params["frameId"].as_str().unwrap_or(""),
                    params["reason"] == "swap",
                );
                if changed {
                    page.revision += 1;
                    page.observation = None;
                    page.handle["resource_revision"] = json!(page.revision.to_string());
                }
            }
        } else if method == "Page.loadEventFired" {
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            for page in state.pages.values_mut().filter(|p| p.session == session) {
                page.loading = false;
            }
        } else if method == "Runtime.executionContextCreated" {
            let context = &params["context"];
            if context["auxData"]["isDefault"] != true {
                return Ok(());
            }
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            for page in state.pages.values_mut() {
                if let Some(frame) = page
                    .frames
                    .values_mut()
                    .find(|f| f.session == session && f.native == context["auxData"]["frameId"])
                {
                    frame.context = context["uniqueId"].as_str().map(str::to_owned);
                }
            }
        } else if method == "Runtime.executionContextsCleared" {
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            for page in state
                .pages
                .values_mut()
                .filter(|p| p.frames.values().any(|f| f.session == session))
            {
                for frame in page.frames.values_mut().filter(|f| f.session == session) {
                    frame.context = None;
                }
                page.observation = None;
            }
        } else if method == "Target.targetDestroyed" {
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            for page in state
                .pages
                .values_mut()
                .filter(|p| p.target == params["targetId"])
            {
                page.handle["state"] = json!("CLOSED");
                page.handle["availability"] = json!("unavailable");
            }
        }
        match method {
            "Target.attachedToTarget" if params["targetInfo"]["type"] == "page" => {
                self.emit("page_created", "active")
            }
            "Target.targetDestroyed" => self.emit("page_closed", "closed"),
            "Page.frameNavigated" => self.emit("navigation", "changed"),
            "Page.frameAttached" | "Page.frameDetached" => self.emit("frame", "changed"),
            "Page.javascriptDialogOpening" => self.emit("dialog", "dismissed"),
            _ => {}
        }
        Ok(())
    }
    fn frame(&self, session: &str, native: &Value) -> Result<(), RacpError> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let native_id = native["id"].as_str().unwrap_or("");
        if let Some(page) = state.pages.values_mut().find(|p| {
            p.session == session || p.frames.values().any(|f| f.active && f.native == native_id)
        }) {
            if page.frames.len() >= 512 || page.frames.values().filter(|f| f.active).count() >= 128
            {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let id = page
                .frames
                .iter()
                .find(|(_, f)| f.active && f.native == native_id)
                .map(|(id, _)| id.clone())
                .unwrap_or_else(|| new_id("frame"));
            let main = native["parentId"].is_null() && page.session == session;
            if main {
                page.main = id.clone();
            }
            let frame = page.frames.entry(id.clone()).or_insert(Frame {
                id,
                native: native_id.into(),
                parent: None,
                session: session.into(),
                context: None,
                url: String::new(),
                name: String::new(),
                active: true,
            });
            if let Some(parent) = native["parentId"].as_str() {
                frame.parent = Some(parent.into());
            }
            if let Some(url) = native["url"].as_str() {
                frame.url = url.into();
            }
            if let Some(name) = native["name"].as_str() {
                frame.name = name.into();
            }
            page.revision += 1;
            page.handle["resource_revision"] = json!(page.revision.to_string());
            page.observation = None;
        }
        Ok(())
    }
}
impl Provider for Browser {
    fn events_after(&self, after: u64) -> Vec<Value> {
        self.outbox
            .lock()
            .map(|events| events.pending(after))
            .unwrap_or_default()
    }
    fn event_cursor(&self) -> String {
        self.outbox
            .lock()
            .map(|events| events.cursor())
            .unwrap_or_else(|_| "0".into())
    }
    fn event_sent(&self, instance: &str, sequence: &str) -> Result<(), RacpError> {
        if instance != self.instance {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.outbox
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .sent(sequence)
    }
    fn event_ack(&self, instance: &str, sequence: &str) -> Result<(), RacpError> {
        if instance != self.instance {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.outbox
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .ack(sequence)
    }
    fn capabilities(&self) -> Vec<Value> {
        let installed = self.config.executable.as_ref().is_some_and(|p| p.is_file());
        let mut operations = if installed {
            vec![
                "browser.open",
                "browser.pages",
                "browser.new_page",
                "browser.close",
                "browser.keepalive",
                "browser.close_page",
                "browser.navigate",
                "browser.snapshot",
                "browser.frames",
                "browser.screenshot",
                "browser.download",
                "browser.upload",
                "browser.click",
                "browser.type",
                "browser.key",
                "browser.evaluate",
            ]
        } else {
            vec![]
        };
        if installed || self.config.cdp_enabled {
            operations.extend(["browser.attach", "browser.cdp_targets"]);
            if !installed {
                operations.extend([
                    "browser.pages",
                    "browser.new_page",
                    "browser.close",
                    "browser.keepalive",
                    "browser.close_page",
                    "browser.navigate",
                    "browser.snapshot",
                    "browser.frames",
                    "browser.screenshot",
                    "browser.download",
                    "browser.upload",
                    "browser.click",
                    "browser.type",
                    "browser.key",
                    "browser.evaluate",
                ]);
            }
        }
        let available = installed || self.config.cdp_enabled;
        let ready = self.ready.load(std::sync::atomic::Ordering::SeqCst);
        vec![
            json!({"name":"browser","version":"1.0.0","operations":operations,"installed":available,"supported":true,"enabled":true,"healthy":available && ready,"unavailable_reason":if !ready {json!("BROWSER_CLEANUP_UNKNOWN")}else if available {Value::Null}else{json!("BROWSER_RUNTIME_UNAVAILABLE")},"attributes":{"engine":"chromium","automation":"native_cdp","cdp_enabled":self.config.cdp_enabled}}),
        ]
    }
    fn inventory(&self) -> Vec<Value> {
        self.sessions
            .lock()
            .map(|sessions| {
                sessions
                    .values()
                    .flat_map(|session| {
                        session
                            .state
                            .lock()
                            .map(|s| {
                                std::iter::once(s.handle.clone())
                                    .chain(s.pages.values().map(|p| p.handle.clone()))
                                    .collect::<Vec<_>>()
                            })
                            .unwrap_or_default()
                    })
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
            if !self.ready.load(std::sync::atomic::Ordering::SeqCst) {
                return Err(RacpError::new("RESOURCE_BUSY"));
            }
            let timeout =
                Duration::from_millis(request["remaining_timeout_ms"].as_u64().unwrap_or(30000));
            let scope = cancel.child_token();
            let effect = std::sync::atomic::AtomicBool::new(false);
            let run = self.run(&request, &scope, &effect);
            tokio::pin!(run);
            // Keep the execution future alive after cancellation so startup can reap its
            // child and remove its profile before the terminal outcome is recorded.
            let (mut result, timeout_elapsed) = tokio::select! { biased;
                result=&mut run=>(result,false),
                _=cancel.cancelled()=>{scope.cancel();(tokio::time::timeout(Duration::from_secs(6),&mut run).await.unwrap_or_else(|_|Err(RacpError::new("EXECUTION_UNKNOWN"))),false)},
                _=tokio::time::sleep(timeout)=>{scope.cancel();(tokio::time::timeout(Duration::from_secs(6),&mut run).await.unwrap_or_else(|_|Err(RacpError::new("EXECUTION_UNKNOWN"))),true)},
            };
            if timeout_elapsed && result.as_ref().is_err_and(|e| e.code.0 == "CANCELLED") {
                result = Err(RacpError::new("TIMEOUT"));
            }
            if result
                .as_ref()
                .is_err_and(|e| matches!(e.code.0, "CANCELLED" | "TIMEOUT"))
            {
                let session = self.get(&request).ok();
                if let Some(session) = session {
                    if session.close().await.is_err() {
                        result = Err(RacpError::new("EXECUTION_UNKNOWN"));
                    }
                } else if matches!(
                    request["operation"].as_str(),
                    Some("browser.open" | "browser.attach")
                ) {
                    let sessions = self
                        .sessions
                        .lock()
                        .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
                        .values()
                        .filter(|s| {
                            s.opening_operation == request["operation_id"].as_str().unwrap_or("")
                                && s.handle()["state"] == "CREATING"
                        })
                        .cloned()
                        .collect::<Vec<_>>();
                    for session in sessions {
                        if session.close().await.is_err() {
                            result = Err(RacpError::new("EXECUTION_UNKNOWN"));
                        }
                    }
                }
            }
            if let Ok(ref mut result) = result {
                if result.is_object() {
                    if let Ok(session) = self.get(&request) {
                        let state = session
                            .state
                            .lock()
                            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                        result["events"] = json!(state.history);
                        if result.get("navigation_revision").is_none() {
                            if let Some(page) = state
                                .pages
                                .get(request["payload"]["page_id"].as_str().unwrap_or(""))
                            {
                                result["navigation_revision"] = json!(page.revision.to_string());
                            }
                        }
                    }
                }
            }
            Ok(match result {
                Ok(result) => json!({"state":"SUCCEEDED","result":result,"error":null}),
                Err(error) => {
                    json!({"state":match error.code.0 {"CANCELLED"=>"CANCELLED","TIMEOUT"=>"TIMED_OUT","CLEANUP_FAILED"|"EXECUTION_UNKNOWN"=>"UNKNOWN",_=>"FAILED"},"result":null,"error":error_value(error.code.0,"browser operation failed",if effect.load(std::sync::atomic::Ordering::SeqCst) {"unknown"} else {"not_started"})})
                }
            })
        })
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            if !self.ready.load(std::sync::atomic::Ordering::SeqCst) {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            let sessions = self
                .sessions
                .lock()
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
                .values()
                .cloned()
                .collect::<Vec<_>>();
            for session in sessions {
                session.close().await?;
            }
            Ok(())
        })
    }
}

#[cfg(test)]
mod frame_race_tests {
    use super::*;
    #[test]
    fn late_parent_swap_preserves_new_child_context_but_remove_invalidates_it() {
        let child = Frame {
            id: "frame_child".into(),
            native: "native_child".into(),
            parent: Some("native_main".into()),
            session: "replacement_session".into(),
            context: Some("new_context".into()),
            url: String::new(),
            name: String::new(),
            active: true,
        };
        let mut page = Page {
            browser_context: String::new(),
            target: "target_main".into(),
            session: "parent_session".into(),
            handle: json!({}),
            revision: 1,
            main: "frame_main".into(),
            frames: BTreeMap::from([("frame_child".into(), child)]),
            observation: None,
            refs: BTreeMap::new(),
            loading: false,
            blocked: false,
        };
        detach(&mut page, "parent_session", "native_child", true);
        assert_eq!(
            page.frames["frame_child"].context.as_deref(),
            Some("new_context")
        );
        detach(&mut page, "parent_session", "native_child", false);
        assert!(!page.frames["frame_child"].active);
        assert!(page.frames["frame_child"].context.is_none());
    }
}
