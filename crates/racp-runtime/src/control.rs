use crate::{identity::process_created, Agent};
use racp_contract::{canonical_digest, digest, RacpError};
use racp_core::{load_settings, private_dir, validate_local_path, InstanceLock, SecretStore};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
};
use tokio_util::sync::CancellationToken;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    version: u8,
    pid: u32,
    created: f64,
    port: u16,
    instance_id: String,
    secret: String,
    launch_digest: String,
}
fn token() -> String {
    use base64::Engine;
    let bytes: [u8; 32] = rand::random();
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}
fn load_record(state: &Path) -> Result<Option<Record>, RacpError> {
    let path = state.join("background/control.bin");
    validate_local_path(&path)?;
    if !path.try_exists()? {
        return Ok(None);
    }
    let value = SecretStore::new(path).load()?;
    let record: Record = serde_json::from_str(
        value
            .get("record")
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
    )?;
    if record.version != 1
        || record.pid == 0
        || record.port == 0
        || !record.created.is_finite()
        || record.created <= 0.0
        || !valid_token(&record.instance_id)
        || !valid_token(&record.secret)
        || record.launch_digest.len() != 64
    {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    Ok(Some(record))
}
fn valid_token(s: &str) -> bool {
    (20..=128).contains(&s.len())
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}
fn matching(record: &Record) -> bool {
    process_created(record.pid).is_ok_and(|created| created == record.created)
}
async fn request(record: &Record, action: &str) -> Result<Value, RacpError> {
    if !matching(record) {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    tokio::time::timeout(Duration::from_secs(2), async {
        let mut socket = TcpStream::connect(("127.0.0.1", record.port)).await?;
        let nonce = token();
        socket
            .write_all(
                format!(
                    "{}\n",
                    json!({"action":action,"secret":record.secret,"nonce":nonce})
                )
                .as_bytes(),
            )
            .await?;
        let mut reader = BufReader::new(socket.take(16385));
        let mut raw = vec![];
        reader.read_until(b'\n', &mut raw).await?;
        if raw.is_empty() || raw.len() > 16384 || raw.last() != Some(&b'\n') {
            return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
        }
        let mut value: Value = serde_json::from_slice(&raw)?;
        if value["nonce"] != nonce
            || value["instance_id"] != record.instance_id
            || value["pid"] != record.pid
            || value["created"].as_f64() != Some(record.created)
        {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        value
            .as_object_mut()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?
            .remove("nonce");
        value.as_object_mut().unwrap().remove("instance_id");
        Ok(value)
    })
    .await
    .map_err(|_| RacpError::new("REQUEST_TIMEOUT"))?
}
#[derive(Clone)]
pub struct ControlClient {
    state: PathBuf,
    executable: PathBuf,
}
impl ControlClient {
    pub fn new(state: PathBuf, executable: PathBuf) -> Self {
        Self { state, executable }
    }
    pub async fn maintenance_stop(&self, install: &Path) -> Result<Value,RacpError> {
        if let Some(record)=load_record(&self.state)?.filter(matching) {
            let system=sysinfo::System::new_all();
            let exe=system.process(sysinfo::Pid::from_u32(record.pid)).and_then(|p|p.exe()).ok_or_else(||RacpError::new("PRECONDITION_FAILED"))?;
            validate_local_path(exe)?;
            if exe!=self.executable || !racp_core::path_within(exe,install) || !matching(&record) {return Err(RacpError::new("PERMISSION_DENIED"));}
        }
        self.stop().await
    }
    pub async fn status(&self) -> Result<Value, RacpError> {
        match load_record(&self.state)? {
            Some(record) if matching(&record) => request(&record, "status").await,
            _ => Ok(json!({"state":"STOPPED","connected":false})),
        }
    }
    pub async fn start(&self) -> Result<Value, RacpError> {
        let background = self.state.join("background");
        let _lock = InstanceLock::acquire(&background.join("controller.lock"))?;
        let (settings, _) = load_settings(&self.state, true)?;
        let expected = canonical_digest(&serde_json::to_value(&settings)?);
        if let Some(record) = load_record(&self.state)? {
            if matching(&record) {
                if record.launch_digest != expected {
                    return Err(RacpError::new("SETTINGS_BUSY"));
                }
                return request(&record, "status").await;
            }
        }
        let mut child: crate::launcher::BackgroundChild =
            crate::launcher::spawn(&self.executable, &self.state)?;
        let pid = child.id();
        for _ in 0..300 {
            if let Some(record) = load_record(&self.state)? {
                if record.pid == pid && matching(&record) {
                    return request(&record, "status").await;
                }
            }
            if child.exited()? {
                return Err(RacpError::new("RUNTIME_UNAVAILABLE"));
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        Err(RacpError::new("REQUEST_TIMEOUT"))
    }
    pub async fn stop(&self) -> Result<Value, RacpError> {
        let _lock = InstanceLock::acquire(&self.state.join("background/controller.lock"))?;
        let Some(record) = load_record(&self.state)?.filter(matching) else {
            return Ok(json!({"state":"STOPPED","connected":false}));
        };
        request(&record, "status").await?;
        request(&record, "stop").await?;
        for _ in 0..400 {
            if !matching(&record) {
                let receipt = SecretStore::new(self.state.join("background/shutdown.bin"))
                    .load()
                    .unwrap_or_default();
                let complete = receipt.get("instance_id") == Some(&record.instance_id)
                    && receipt.get("cleanup_status").map(String::as_str) == Some("complete");
                return Ok(
                    json!({"state":"STOPPED","connected":false,"cleanup_status":if complete{"complete"}else{"unknown"}}),
                );
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        Err(RacpError::new("REQUEST_TIMEOUT"))
    }
}
pub async fn serve(state: &Path) -> Result<(), RacpError> {
    serve_with_browser(state, crate::providers::browser::BrowserConfig::bundled()).await
}
pub async fn serve_with_browser(
    state: &Path,
    config: crate::providers::browser::BrowserConfig,
) -> Result<(), RacpError> {
    let (settings, credentials) = load_settings(state, true)?;
    let _agent_lock =
        InstanceLock::acquire(&state.join(format!("agent-{}.lock", digest(&settings.device_id))))?;
    let launch_digest = canonical_digest(&serde_json::to_value(&settings)?);
    let agent = Agent::with_browser(settings, credentials["credential"].clone(), config)?;
    let background = state.join("background");
    private_dir(&background)?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let record = Record {
        version: 1,
        pid: std::process::id(),
        created: process_created(std::process::id())?,
        port: listener.local_addr()?.port(),
        instance_id: token(),
        secret: token(),
        launch_digest,
    };
    let path = background.join("control.bin");
    SecretStore::new(path.clone()).save(
        &BTreeMap::from([("record".into(), serde_json::to_string(&record)?)]),
        true,
    )?;
    let shutdown = CancellationToken::new();
    let stopped = shutdown.clone();
    let snapshot_agent = agent.clone();
    let control_record = record.clone();
    let server = tokio::spawn(async move {
        let limit = std::sync::Arc::new(tokio::sync::Semaphore::new(8));
        let mut clients = tokio::task::JoinSet::new();
        loop {
            tokio::select! {
             _=stopped.cancelled()=>break,
             completed=clients.join_next(),if !clients.is_empty()=>{let _=completed;},
             connection=listener.accept()=>{
              let Ok((socket,_))=connection else{break};let Ok(permit)=limit.clone().try_acquire_owned()else{continue};let agent=snapshot_agent.clone();let record=control_record.clone();let cancel=stopped.clone();
              clients.spawn(async move{let _permit=permit;let _=tokio::time::timeout(Duration::from_secs(2),accepted(socket,&agent,&record,cancel)).await;});
             }
            }
        }
        clients.abort_all();
        while clients.join_next().await.is_some() {}
    });
    let result = agent.run(shutdown.clone()).await;
    shutdown.cancel();
    let _ = server.await;
    if result.is_ok() {
        SecretStore::new(background.join("shutdown.bin")).save(
            &BTreeMap::from([
                ("instance_id".into(), record.instance_id.clone()),
                ("cleanup_status".into(), "complete".into()),
            ]),
            true,
        )?;
    }
    if load_record(state)?.is_some_and(|current| current.instance_id == record.instance_id) {
        std::fs::remove_file(path)?;
    }
    result
}
async fn accepted(
    socket: TcpStream,
    agent: &Agent,
    record: &Record,
    shutdown: CancellationToken,
) -> Result<(), RacpError> {
    use subtle::ConstantTimeEq;
    let mut reader = BufReader::new(socket.take(4097));
    let mut raw = vec![];
    reader.read_until(b'\n', &mut raw).await?;
    if raw.is_empty() || raw.len() > 4096 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ControlRequest {
        action: String,
        secret: String,
        nonce: String,
    }
    let request: ControlRequest = serde_json::from_slice(&raw)?;
    if !matches!(request.action.as_str(), "status" | "stop")
        || !valid_token(&request.nonce)
        || !valid_token(&request.secret)
        || !bool::from(request.secret.as_bytes().ct_eq(record.secret.as_bytes()))
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let mut snapshot = agent.snapshot().await;
    snapshot["pid"] = json!(record.pid);
    snapshot["created"] = json!(record.created);
    snapshot["instance_id"] = json!(record.instance_id);
    snapshot["nonce"] = json!(request.nonce);
    if request.action == "stop" {
        snapshot["state"] = json!("STOPPING");
    }
    let mut socket = reader.into_inner().into_inner();
    socket.write_all(format!("{snapshot}\n").as_bytes()).await?;
    socket.shutdown().await?;
    if request.action == "stop" {
        shutdown.cancel();
    }
    Ok(())
}
pub fn activity(state: &Path) -> Result<Value, RacpError> {
    use std::io::{Read, Seek, SeekFrom};
    let path = state.join("background/agent.log");
    validate_local_path(&path)?;
    if !path.try_exists()? {
        return Ok(json!({"events":[],"tail_limited":false}));
    }
    let mut file = std::fs::File::open(path)?;
    let length = file.metadata()?.len();
    let offset = length.saturating_sub(32768);
    file.seek(SeekFrom::Start(offset))?;
    let mut raw = vec![];
    file.take(32768).read_to_end(&mut raw)?;
    let text = String::from_utf8_lossy(&raw);
    let mut events = vec![];
    for (i, line) in text.lines().enumerate() {
        if i == 0 && offset > 0 {
            continue;
        }
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if ![
            "agent_connected",
            "agent_disconnected",
            "agent_handshake_rejected",
            "agent_execution_started",
            "agent_result",
        ]
        .contains(&value["event"].as_str().unwrap_or(""))
        {
            continue;
        }
        let mut event = json!({"event":value["event"]});
        if value["timestamp"]
            .as_str()
            .is_some_and(|s| s.len() <= 40 && chrono::DateTime::parse_from_rfc3339(s).is_ok())
        {
            event["timestamp"] = value["timestamp"].clone();
        }
        if value["operation"]
            .as_str()
            .is_some_and(|s| racp_contract::registry().get(s).is_some())
        {
            event["operation"] = value["operation"].clone();
        }
        if value["state"].as_str().is_some_and(|s| {
            [
                "ACCEPTED",
                "DISPATCHED",
                "RUNNING",
                "CANCEL_REQUESTED",
                "RECONCILING",
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
                "TIMED_OUT",
                "UNKNOWN",
            ]
            .contains(&s)
        }) {
            event["state"] = value["state"].clone();
        }
        if let Some(id) = value["operation_id"].as_str() {
            if id.starts_with("op_")
                && id.len() == 35
                && id[3..]
                    .chars()
                    .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            {
                event["operation_id"] = json!(id);
            }
        }
        events.push(event);
    }
    let limited = offset > 0 || events.len() > 40;
    let keep = events.len().saturating_sub(40);
    Ok(json!({"events":&events[keep..],"tail_limited":limited}))
}
