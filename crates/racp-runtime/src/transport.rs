use crate::{enrollment::http_client, peer::Peer};
use futures_util::StreamExt;
use racp_contract::{decode_message, new_id, timestamp, RacpError, VERSION};
use racp_core::{AgentSettings, Journal, OutputSpool};
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tokio::sync::{Mutex, RwLock};
use tokio_tungstenite::{
    connect_async_tls_with_config,
    tungstenite::{client::IntoClientRequest, protocol::WebSocketConfig, Message as Frame},
    Connector,
};
use tokio_util::sync::CancellationToken;
#[derive(Clone)]
pub struct Agent {
    pub settings: AgentSettings,
    pub journal: Journal,
    pub boot_id: String,
    credential: Arc<String>,
    pub(crate) artifacts: crate::artifacts::ArtifactClient,
    pub(crate) providers: Arc<dyn crate::providers::Provider>,
    pub(crate) outputs: OutputSpool,
    pub(crate) tasks: Arc<Mutex<std::collections::BTreeMap<String, crate::dispatch::Execution>>>,
    pub(crate) completed: Arc<tokio::sync::Notify>,
    pub(crate) status: Arc<RwLock<Value>>,
    pub(crate) peer: Arc<RwLock<Option<Peer>>>,
    pub(crate) lease: Arc<Mutex<tokio::time::Instant>>,
}
impl Agent {
    pub fn new(settings: AgentSettings, credential: String) -> Result<Self, RacpError> {
        Self::with_browser(
            settings,
            credential,
            crate::providers::browser::BrowserConfig::bundled(),
        )
    }
    pub fn with_browser(
        settings: AgentSettings,
        credential: String,
        browser: crate::providers::browser::BrowserConfig,
    ) -> Result<Self, RacpError> {
        settings.validate(true)?;
        let artifacts = crate::artifacts::ArtifactClient::new(
            &settings.gateway,
            &credential,
            &settings.device_id,
            settings.ca_file.as_deref(),
        )?;
        let journal = Journal::open(&settings.data_dir.join("execution.db"))?;
        journal.recover_agent()?;
        let boot_id = new_id("boot");
        let providers: Arc<dyn crate::providers::Provider> = Arc::new(
            crate::providers::NativeProviders::with_browser(&settings, &boot_id, browser)?,
        );
        let outputs = OutputSpool::new(settings.data_dir.join("spool"), journal.clone())?;
        let status = json!({"state":"RUNNING","connected":false,"execution_identity":whoami::username(),"connection_epoch":0,"connection_phase":"starting","active_operations":0,"desktop":{"enabled":settings.desktop_enabled,"healthy":false,"unavailable_reason":"DESKTOP_RUNTIME_UNAVAILABLE","sessions":[]},"operations":[]});
        Ok(Self {
            settings,
            journal,
            outputs,
            tasks: Arc::new(Mutex::new(std::collections::BTreeMap::new())),
            completed: Arc::new(tokio::sync::Notify::new()),
            boot_id,
            credential: Arc::new(credential),
            artifacts,
            providers,
            status: Arc::new(RwLock::new(status)),
            peer: Arc::new(RwLock::new(None)),
            lease: Arc::new(Mutex::new(tokio::time::Instant::now())),
        })
    }
    pub async fn snapshot(&self) -> Value {
        let mut status = self.status.read().await.clone();
        status["connected"] = json!(
            status["connected"] == true && tokio::time::Instant::now() < *self.lease.lock().await
        );
        status["active_operations"] = json!(self.tasks.lock().await.len());
        status["operations"] = json!(self.execution_inventory().await.unwrap_or_default());
        status
    }
    async fn phase(&self, phase: &str) {
        self.status.write().await["connection_phase"] = json!(phase);
    }
    pub(crate) async fn send(&self, value: Value) -> Result<(), RacpError> {
        let peer = self
            .peer
            .read()
            .await
            .clone()
            .ok_or_else(|| RacpError::new("DEVICE_OFFLINE"))?;
        peer.send(value).await
    }
    pub(crate) async fn base(&self, kind: &str) -> Value {
        json!({"protocol":1,"type":kind,"device_id":self.settings.device_id,"agent_boot_id":self.boot_id,"connection_epoch":self.status.read().await["connection_epoch"]})
    }
    async fn heartbeat(&self) -> Result<(), RacpError> {
        let mut value = self.base("heartbeat").await;
        value["health"] = json!("healthy");
        value["handles"] = json!(self.providers.inventory());
        value["capabilities"] = json!(self.providers.capabilities());
        self.send(value).await
    }
    async fn browser_event(&self, after: &mut u64) -> Result<(), RacpError> {
        if tokio::time::Instant::now() >= *self.lease.lock().await {
            return Ok(());
        }
        let Some(event) = self.providers.events_after(*after).into_iter().next() else {
            return Ok(());
        };
        let mut value = self.base("browser_state").await;
        for (key, field) in event
            .as_object()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?
        {
            value[key] = field.clone();
        }
        self.send(value).await?;
        let instance = event["provider_instance_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
        let sequence = event["event_sequence"]
            .as_str()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
        self.providers.event_sent(instance, sequence)?;
        *after = sequence
            .parse()
            .map_err(|_| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
        Ok(())
    }
    async fn session(&self, shutdown: &CancellationToken) -> Result<(), RacpError> {
        self.phase("connecting").await;
        let mut url = url::Url::parse(&self.settings.gateway)
            .map_err(|_| RacpError::new("GATEWAY_INVALID"))?;
        let scheme = if url.scheme() == "https" { "wss" } else { "ws" };
        url.set_scheme(scheme)
            .map_err(|_| RacpError::new("GATEWAY_INVALID"))?;
        url.set_path("/agent/v1/connect");
        let mut request = url
            .as_str()
            .into_client_request()
            .map_err(|_| RacpError::new("GATEWAY_INVALID"))?;
        request.headers_mut().insert(
            "Authorization",
            format!("Bearer {}", self.credential)
                .parse()
                .map_err(|_| RacpError::new("CONFIG_UNREADABLE"))?,
        );
        let mut roots = rustls::RootCertStore::empty();
        roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
        if let Some(ca) = &self.settings.ca_file {
            for cert in racp_core::validate_ca(&racp_core::read_bounded(ca, 1024 * 1024, false)?)? {
                roots.add(cert).map_err(|_| RacpError::new("CA_INVALID"))?;
            }
        }
        let tls = rustls::ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let config = WebSocketConfig::default()
            .max_message_size(Some(1024 * 1024))
            .max_frame_size(Some(1024 * 1024));
        let connection = tokio::select! { _=shutdown.cancelled()=>return Ok(()), connection=tokio::time::timeout(
            Duration::from_secs(10),
            connect_async_tls_with_config(
                request,
                Some(config),
                true,
                Some(Connector::Rustls(Arc::new(tls))),
            ),
        )
        =>connection.map_err(|_| RacpError::new("GATEWAY_TIMEOUT"))? };
        let (socket, _) = connection.map_err(|e| match e {
            tokio_tungstenite::tungstenite::Error::Http(r)
                if matches!(r.status().as_u16(), 401 | 403) =>
            {
                RacpError::new("TOKEN_REJECTED")
            }
            _ => RacpError::new("GATEWAY_UNREACHABLE"),
        })?;
        let (sink, mut stream) = socket.split();
        let cancel = shutdown.child_token();
        let peer = Peer::spawn(sink, cancel.clone());
        let streams = crate::streams::Streams::new(self.clone(), peer.clone());
        *self.peer.write().await = Some(peer);
        let result=async{
   self.phase("hello").await;
   let platform=if cfg!(windows){"Windows"}else if cfg!(target_os="macos"){"Darwin"}else{"Linux"};
   self.send(json!({"protocol":1,"type":"hello","device_id":self.settings.device_id,"agent_boot_id":self.boot_id,"agent_version":VERSION,"supported_protocols":[1],"platform":platform,"architecture":std::env::consts::ARCH,"execution_identity":whoami::username(),"capabilities":self.providers.capabilities(),"last_event_cursor":self.providers.event_cursor()})).await?;
   self.phase("welcome").await;
   let welcome=tokio::select!{_=shutdown.cancelled()=>return Ok(()),v=receive(&mut stream)=>v?};
   if welcome["type"]!="welcome"||welcome["device_id"]!=self.settings.device_id||welcome["agent_boot_id"]!=self.boot_id{return Err(RacpError::new("REQUEST_INVALID"));}
   let epoch=welcome["connection_epoch"].as_u64().ok_or_else(||RacpError::new("REQUEST_INVALID"))?;self.status.write().await["connection_epoch"]=json!(epoch);
   self.phase("reconcile").await;
   for record in self.journal.records()?{
    if record.request["device_id"]!=self.settings.device_id||!Journal::terminal(&record.state){continue;}
    let mut value=self.base("reconcile").await;value["complete"]=json!(false);value["handles"]=json!([]);value["active"]=json!([]);value["records"]=json!([]);value["expired"]=json!([]);
    let outcome=self.outcome(record).await?;
    if outcome["type"]=="result" { value["records"]=json!([outcome]); }
    else {value["expired"]=json!([outcome]);}
    self.send(value).await?;
   }
   let mut reconcile=self.base("reconcile").await;reconcile["records"]=json!([]);reconcile["complete"]=json!(true);reconcile["handles"]=json!(self.providers.inventory());reconcile["active"]=json!(self.execution_inventory().await?);reconcile["expired"]=json!([]);self.send(reconcile).await?;
   let ack=tokio::select!{_=shutdown.cancelled()=>return Ok(()),v=receive(&mut stream)=>v?};if ack["type"]!="heartbeat"||ack["connection_epoch"]!=epoch||ack["device_id"]!=self.settings.device_id||ack["agent_boot_id"]!=self.boot_id{return Err(RacpError::new("REQUEST_INVALID"));}
   let lease_ms=welcome["execution_lease_ttl_ms"].as_u64().filter(|n|*n>0&&*n<=86400000).ok_or_else(||RacpError::new("REQUEST_INVALID"))?;
   let interval_ms=welcome["heartbeat_interval_ms"].as_u64().filter(|n|*n>0&&*n<=60000).ok_or_else(||RacpError::new("REQUEST_INVALID"))?;
   *self.lease.lock().await=tokio::time::Instant::now()+Duration::from_millis(lease_ms);self.phase("ready").await;self.status.write().await["connected"]=json!(true);
   self.log("agent_connected",json!({"connection_epoch":epoch}));
   let mut timer=tokio::time::interval(Duration::from_millis(interval_ms));timer.tick().await;
   let mut browser_timer=tokio::time::interval(Duration::from_millis(50));let mut browser_sequence=0;
   loop{
    tokio::select!{
     _=shutdown.cancelled()=>break,
     _=timer.tick()=>self.heartbeat().await?,
     _=browser_timer.tick()=>self.browser_event(&mut browser_sequence).await?,
     incoming=stream.next()=>{
      let Some(Ok(frame))=incoming else{break};
      let raw=match frame{Frame::Text(s)=>s.as_bytes().to_vec(),Frame::Close(_)=>break,Frame::Ping(_)|Frame::Pong(_)=>continue,_=>return Err(RacpError::new("REQUEST_INVALID"))};
      let message=decode_message(&raw)?;
      if message["device_id"]!=self.settings.device_id||message["agent_boot_id"]!=self.boot_id||message["connection_epoch"]!=epoch{return Err(RacpError::new("STALE_CONNECTION"));}
      if message["type"]=="heartbeat"{*self.lease.lock().await=tokio::time::Instant::now()+Duration::from_millis(lease_ms);}
      else if message["type"]=="request"{self.dispatch(message).await?;}
      else if message["type"]=="cancel"{self.cancel_execution(message["target_operation_id"].as_str().ok_or_else(||RacpError::new("REQUEST_INVALID"))?,message["reason"].as_str().unwrap_or("requested")).await?;}
      else if message["type"]=="stream_open"{streams.subscribe(message).await?;}
      else if message["type"]=="stream_ack"{streams.ack(&message).await?;}
      else if message["type"]=="stream_unsubscribe"{streams.unsubscribe(&message).await?;}
      else if message["type"]=="browser_state_ack"{self.providers.event_ack(message["provider_instance_id"].as_str().ok_or_else(||RacpError::new("REQUEST_INVALID"))?,message["event_sequence"].as_str().ok_or_else(||RacpError::new("REQUEST_INVALID"))?)?;}
      else{return Err(RacpError::new("REQUEST_INVALID"));}
     }
    }
   }Ok(())
  }.await;
        cancel.cancel();
        streams.close().await;
        if let Some(peer) = self.peer.write().await.take() {
            peer.close().await;
        }
        self.status.write().await["connected"] = json!(false);
        self.phase("disconnected").await;
        result
    }
    pub async fn run(&self, shutdown: CancellationToken) -> Result<(), RacpError> {
        let _ = http_client(self.settings.ca_file.as_deref())?;
        let agent = self.clone();
        let watched = shutdown.clone();
        let watchdog = tokio::spawn(async move {
            let mut retired = false;
            loop {
                tokio::select! {_=watched.cancelled()=>break,_=tokio::time::sleep(Duration::from_millis(100))=>{}}
                let expired = tokio::time::Instant::now() >= *agent.lease.lock().await;
                if expired && !retired {
                    agent.cancel_all("lease").await?;
                    agent.providers.cleanup().await?;
                    retired = true;
                }
                if !expired {
                    retired = false;
                }
            }
            Ok::<(), RacpError>(())
        });
        let retry_agent = self.clone();
        let retry_shutdown = shutdown.clone();
        let output_retry = tokio::spawn(async move {
            loop {
                tokio::select! {_=retry_shutdown.cancelled()=>break,_=tokio::time::sleep(Duration::from_secs(2))=>{}}
                retry_agent.outputs.collect_completed_files()?;
                if !retry_agent.snapshot().await["connected"]
                    .as_bool()
                    .unwrap_or(false)
                {
                    continue;
                }
                for output in retry_agent.outputs.pending()? {
                    let Some(id) = output["id"].as_str() else {
                        continue;
                    };
                    let Some(op) = output["operation_id"].as_str() else {
                        continue;
                    };
                    if retry_agent.tasks.lock().await.contains_key(op) {
                        continue;
                    }
                    let record = retry_agent.journal.get(op)?;
                    if !record.outcome_available || !Journal::terminal(&record.state) {
                        continue;
                    }
                    let attempts = output["attempts"].as_u64().unwrap_or(0).min(6);
                    if let Some(updated) = output["updated_at"]
                        .as_str()
                        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                    {
                        if (chrono::Utc::now() - updated.to_utc()).num_seconds()
                            < (1u64 << attempts).min(60) as i64
                        {
                            continue;
                        }
                    }
                    let result = tokio::select! {_=retry_shutdown.cancelled()=>return Ok::<(),RacpError>(()),v=retry_agent.artifacts.upload(&output,&retry_agent.outputs)=>v};
                    match result {
                        Ok(_) => {
                            let _ = retry_agent.send(retry_agent.outcome(record).await?).await;
                        }
                        Err(e) => retry_agent.outputs.failed(id, e.code.0)?,
                    }
                }
            }
            Ok::<(), RacpError>(())
        });
        let mut delay = Duration::from_millis(500);
        loop {
            if shutdown.is_cancelled() {
                break;
            }
            let result = self.session(&shutdown).await;
            if let Err(error) = result {
                self.log("agent_disconnected", json!({"code":error.code}));
                if error.code.0 == "TOKEN_REJECTED" {
                    shutdown.cancel();
                    break;
                }
            }
            tokio::select! {_ =shutdown.cancelled()=>break,_=tokio::time::sleep(delay)=>()};
            delay = (delay * 2).min(Duration::from_secs(10));
        }
        shutdown.cancel();
        self.cancel_all("shutdown").await?;
        let cleanup = async {
            loop {
                let notified = self.completed.notified();
                if self.tasks.lock().await.is_empty() {
                    break;
                }
                notified.await;
            }
            self.providers.cleanup().await
        };
        tokio::time::timeout(Duration::from_secs(15), cleanup)
            .await
            .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))??;
        output_retry
            .await
            .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))??;
        watchdog
            .await
            .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))??;
        self.status.write().await["connected"] = json!(false);
        self.status.write().await["state"] = json!("STOPPED");
        Ok(())
    }
    pub fn log(&self, event: &str, mut value: Value) {
        use std::io::Write;
        value["event"] = json!(event);
        value["timestamp"] = json!(timestamp());
        let Some(state) = self.settings.data_dir.parent() else {
            return;
        };
        let path = state.join("background/agent.log");
        if let Ok(info) = std::fs::metadata(&path) {
            if info.len() > 1024 * 1024 {
                let _ = std::fs::rename(&path, state.join("background/agent.log.1"));
            }
        }
        let mut options = std::fs::OpenOptions::new();
        options.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        if let Ok(mut file) = options.open(path) {
            let _ = writeln!(file, "{value}");
        }
    }
}
async fn receive<S>(stream: &mut S) -> Result<Value, RacpError>
where
    S: futures_util::Stream<Item = Result<Frame, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    let frame = tokio::time::timeout(Duration::from_secs(10), stream.next())
        .await
        .map_err(|_| RacpError::new("GATEWAY_TIMEOUT"))?
        .ok_or_else(|| RacpError::new("DEVICE_OFFLINE"))?
        .map_err(|_| RacpError::new("DEVICE_OFFLINE"))?;
    match frame {
        Frame::Text(s) => decode_message(s.as_bytes()),
        _ => Err(RacpError::new("REQUEST_INVALID")),
    }
}
