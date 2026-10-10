use futures_util::{SinkExt, StreamExt};
use racp_contract::RacpError;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tokio::sync::{broadcast, mpsc, oneshot, Semaphore};
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{protocol::WebSocketConfig, Message},
};
use tokio_util::sync::CancellationToken;
type Pending = Arc<Mutex<BTreeMap<u64, oneshot::Sender<Result<Value, RacpError>>>>>;
struct Inner {
    commands: mpsc::Sender<Value>,
    pending: Pending,
    events: broadcast::Sender<Value>,
    sequence: AtomicU64,
    permits: Semaphore,
    closed: CancellationToken,
    tasks: tokio::sync::Mutex<Vec<tokio::task::JoinHandle<()>>>,
}
impl Drop for Inner {
    fn drop(&mut self) {
        self.closed.cancel();
    }
}
#[derive(Clone)]
pub struct Cdp(Arc<Inner>);
struct PendingGuard {
    id: u64,
    pending: Pending,
}
impl Drop for PendingGuard {
    fn drop(&mut self) {
        if let Ok(mut pending) = self.pending.lock() {
            pending.remove(&self.id);
        }
    }
}
impl Cdp {
    pub async fn connect(endpoint: &str) -> Result<Self, RacpError> {
        let endpoint = local_endpoint(endpoint)?;
        if !matches!(endpoint.scheme(), "ws" | "wss") {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let config = WebSocketConfig::default()
            .max_message_size(Some(48 * 1024 * 1024))
            .max_frame_size(Some(48 * 1024 * 1024));
        let (socket, _) = tokio::time::timeout(
            Duration::from_secs(5),
            connect_async_with_config(endpoint.as_str(), Some(config), true),
        )
        .await
        .map_err(|_| RacpError::new("TIMEOUT"))?
        .map_err(|_| RacpError::new("BROWSER_UNAVAILABLE"))?;
        let (mut sink, mut stream) = socket.split();
        let (commands, mut command_rx) = mpsc::channel::<Value>(64);
        let (events, _) = broadcast::channel(256);
        let pending: Pending = Arc::default();
        let closed = CancellationToken::new();
        let writer_closed = closed.clone();
        let writer = tokio::spawn(async move {
            loop {
                let command = tokio::select! { _ = writer_closed.cancelled() => break, command = command_rx.recv() => command };
                let Some(command) = command else { break };
                if !matches!(
                    tokio::time::timeout(
                        Duration::from_secs(5),
                        sink.send(Message::Text(command.to_string().into()))
                    )
                    .await,
                    Ok(Ok(()))
                ) {
                    break;
                }
            }
            writer_closed.cancel();
            let _ = tokio::time::timeout(Duration::from_secs(1), sink.close()).await;
        });
        let reader_closed = closed.clone();
        let reader_pending = pending.clone();
        let reader_events = events.clone();
        let reader = tokio::spawn(async move {
            loop {
                let incoming = tokio::select! { _ = reader_closed.cancelled() => break, incoming = stream.next() => incoming };
                let Some(Ok(message)) = incoming else { break };
                let raw = match message {
                    Message::Text(raw) => raw,
                    Message::Close(_) => break,
                    Message::Ping(_) | Message::Pong(_) => continue,
                    _ => break,
                };
                let Ok(value) = serde_json::from_str::<Value>(&raw) else {
                    break;
                };
                if let Some(id) = value["id"].as_u64() {
                    let reply = reader_pending
                        .lock()
                        .ok()
                        .and_then(|mut pending| pending.remove(&id));
                    if let Some(reply) = reply {
                        // CDP diagnostics contain page data; never expose arbitrary Chrome strings.
                        let result = if value.get("error").is_some() {
                            let category = match value["error"]["message"].as_str().unwrap_or("") {
                                "Not attached to an active page" | "Not attached to a page" => {
                                    "inactive_page"
                                }
                                "Session with given id not found." => "missing_session",
                                "Invalid URL pattern" | "Invalid URL pattern." => "invalid_pattern",
                                "Domain must be enabled" | "Fetch domain is not enabled" => {
                                    "disabled_domain"
                                }
                                _ => "other",
                            };
                            eprintln!(
                                "cdp protocol failure category={category} code={}",
                                value["error"]["code"].as_i64().unwrap_or(0)
                            );
                            Err(RacpError::new("EXECUTION_FAILED"))
                        } else {
                            Ok(value["result"].clone())
                        };
                        let _ = reply.send(result);
                    }
                } else if value["method"].is_string() {
                    if let Some(value) = metadata_event(value) {
                        if value.to_string().len() > 64 * 1024 {
                            break;
                        }
                        let _ = reader_events.send(value);
                    }
                } else {
                    break;
                }
            }
            reader_closed.cancel();
            if let Ok(mut pending) = reader_pending.lock() {
                for (_, reply) in std::mem::take(&mut *pending) {
                    let _ = reply.send(Err(RacpError::new("BROWSER_CLOSED")));
                }
            }
        });
        Ok(Self(Arc::new(Inner {
            commands,
            pending,
            events,
            sequence: AtomicU64::new(0),
            permits: Semaphore::new(64),
            closed,
            tasks: tokio::sync::Mutex::new(vec![writer, reader]),
        })))
    }
    pub fn subscribe(&self) -> broadcast::Receiver<Value> {
        self.0.events.subscribe()
    }
    pub fn is_closed(&self) -> bool {
        self.0.closed.is_cancelled()
    }
    pub async fn call(
        &self,
        method: &str,
        params: Value,
        session: Option<&str>,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if self.is_closed() {
            return Err(RacpError::new("BROWSER_CLOSED"));
        }
        let _permit = self
            .0
            .permits
            .try_acquire()
            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
        let id = self.0.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        let (reply, receiver) = oneshot::channel();
        self.0
            .pending
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .insert(id, reply);
        let _pending = PendingGuard {
            id,
            pending: self.0.pending.clone(),
        };
        let mut command = json!({"id":id,"method":method,"params":params});
        if let Some(session) = session {
            command["sessionId"] = json!(session);
        }
        if command.to_string().len() > 1024 * 1024 {
            return Err(RacpError::new("OUTPUT_LIMIT_EXCEEDED"));
        }
        self.0
            .commands
            .try_send(command)
            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
        let outcome = tokio::select! { biased;
            _ = cancel.cancelled() => Err(RacpError::new("CANCELLED")),
            _ = self.0.closed.cancelled() => Err(RacpError::new("BROWSER_CLOSED")),
            result = tokio::time::timeout(Duration::from_secs(30), receiver) => result.map_err(|_| RacpError::new("TIMEOUT"))?.map_err(|_| RacpError::new("BROWSER_CLOSED"))?,
        };
        if outcome
            .as_ref()
            .is_err_and(|error| error.code.0 == "EXECUTION_FAILED")
        {
            eprintln!("cdp command failed method={method}");
        }
        outcome
    }
    pub async fn close(&self) {
        self.0.closed.cancel();
        for task in self.0.tasks.lock().await.drain(..) {
            let _ = task.await;
        }
    }
}

fn metadata_event(value: Value) -> Option<Value> {
    let method = value["method"].as_str()?;
    let p = &value["params"];
    let params = match method {
        "Fetch.requestPaused" => {
            json!({"requestId":p["requestId"],"resourceType":p["resourceType"],"frameId":p["frameId"],"request":{"url":p["request"]["url"]}})
        }
        "Page.javascriptDialogOpening" => json!({}),
        "Target.targetCreated" => {
            json!({"targetInfo":{"targetId":p["targetInfo"]["targetId"],"type":p["targetInfo"]["type"],"browserContextId":p["targetInfo"]["browserContextId"]}})
        }
        "Target.attachedToTarget" => {
            json!({"sessionId":p["sessionId"],"waitingForDebugger":p["waitingForDebugger"],"targetInfo":{"targetId":p["targetInfo"]["targetId"],"type":p["targetInfo"]["type"],"browserContextId":p["targetInfo"]["browserContextId"]}})
        }
        "Runtime.executionContextCreated" => {
            json!({"context":{"id":p["context"]["id"],"uniqueId":p["context"]["uniqueId"],"auxData":p["context"]["auxData"]}})
        }
        "Page.frameNavigated" => {
            json!({"frame":{"id":p["frame"]["id"],"parentId":p["frame"]["parentId"],"url":p["frame"]["url"],"name":p["frame"]["name"]}})
        }
        "Target.detachedFromTarget"
        | "Target.targetDestroyed"
        | "Runtime.executionContextsCleared"
        | "Runtime.executionContextDestroyed"
        | "Page.frameAttached"
        | "Page.frameDetached"
        | "Page.loadEventFired"
        | "Browser.downloadWillBegin"
        | "Browser.downloadProgress" => p.clone(),
        _ => return None,
    };
    Some(json!({"method":method,"sessionId":value["sessionId"],"params":params}))
}

pub fn local_endpoint(value: &str) -> Result<url::Url, RacpError> {
    let mut url = url::Url::parse(value).map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
    if url.host_str() == Some("localhost") {
        url.set_host(Some("127.0.0.1"))
            .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
    }
    let local = match url.host() {
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        _ => false,
    };
    // Url elides default ports: require an explicit port in the original authority too.
    let authority = value
        .split_once("://")
        .map(|(_, rest)| rest.split('/').next().unwrap_or(""))
        .unwrap_or("");
    let explicit = authority
        .rsplit_once(':')
        .is_some_and(|(_, port)| port.parse::<u16>().is_ok_and(|port| port != 0));
    if !local
        || !explicit
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.scheme(), "http" | "https" | "ws" | "wss")
        || matches!(url.scheme(), "http" | "https") && url.path() != "/"
        || matches!(url.scheme(), "ws" | "wss") && !url.path().starts_with("/devtools/browser/")
    {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    Ok(url)
}

pub async fn discover(value: &str) -> Result<String, RacpError> {
    let mut endpoint = local_endpoint(value)?;
    if matches!(endpoint.scheme(), "ws" | "wss") {
        return Ok(endpoint.to_string());
    }
    endpoint.set_path("/json/version");
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|_| RacpError::new("BROWSER_UNAVAILABLE"))?;
    let response = client
        .get(endpoint.clone())
        .send()
        .await
        .map_err(|_| RacpError::new("BROWSER_UNAVAILABLE"))?;
    if !response.status().is_success() {
        return Err(RacpError::new("BROWSER_UNAVAILABLE"));
    }
    let mut chunks = response.bytes_stream();
    let mut raw = vec![];
    while let Some(chunk) = chunks.next().await {
        let chunk = chunk.map_err(|_| RacpError::new("BROWSER_UNAVAILABLE"))?;
        if raw.len() + chunk.len() > 64 * 1024 {
            return Err(RacpError::new("OUTPUT_LIMIT_EXCEEDED"));
        }
        raw.extend_from_slice(&chunk);
    }
    let value: Value =
        serde_json::from_slice(&raw).map_err(|_| RacpError::new("BROWSER_UNAVAILABLE"))?;
    let socket = local_endpoint(value["webSocketDebuggerUrl"].as_str().unwrap_or(""))?;
    let scheme = if endpoint.scheme() == "https" {
        "wss"
    } else {
        "ws"
    };
    if socket.host() != endpoint.host()
        || socket.port_or_known_default() != endpoint.port_or_known_default()
        || socket.scheme() != scheme
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(socket.to_string())
}
