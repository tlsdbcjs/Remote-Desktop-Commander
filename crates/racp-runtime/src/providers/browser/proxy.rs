//! Context-owned proxy gates the first network request, including popup navigations.
use super::BrowserConfig;
use futures_util::StreamExt;
use http_body_util::{combinators::UnsyncBoxBody, BodyDataStream, BodyExt, Full, StreamBody};
use hyper::{
    body::{Bytes, Frame, Incoming},
    header,
    server::conn::http1,
    service::service_fn,
    Request, Response, StatusCode,
};
use hyper_util::rt::TokioIo;
use racp_contract::RacpError;
use std::{convert::Infallible, net::SocketAddr, sync::Arc};
use tokio::{
    net::{TcpListener, TcpStream},
    sync::{Mutex, Semaphore},
    task::{JoinHandle, JoinSet},
};
use tokio_util::sync::CancellationToken;
type Error = Box<dyn std::error::Error + Send + Sync>;
type Body = UnsyncBoxBody<Bytes, Error>;
struct UpgradeTasks {
    tasks: Mutex<JoinSet<()>>,
    limit: Arc<Semaphore>,
}
type Upgrades = Arc<UpgradeTasks>;
pub struct PolicyProxy {
    address: SocketAddr,
    stop: CancellationToken,
    worker: Mutex<Option<JoinHandle<()>>>,
}
impl Drop for PolicyProxy {
    fn drop(&mut self) {
        self.stop.cancel();
    }
}
impl PolicyProxy {
    pub async fn bind(config: BrowserConfig) -> Result<Self, RacpError> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let address = listener.local_addr()?;
        let stop = CancellationToken::new();
        let stopped = stop.clone();
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(std::time::Duration::from_secs(5))
            .build()
            .map_err(|_| RacpError::new("BROWSER_UNAVAILABLE"))?;
        let worker = tokio::spawn(async move {
            let limit = Arc::new(Semaphore::new(64));
            let upgrades: Upgrades = Arc::new(UpgradeTasks {
                tasks: Mutex::new(JoinSet::new()),
                limit: Arc::new(Semaphore::new(64)),
            });
            let mut connections = JoinSet::new();
            let mut collect = tokio::time::interval(std::time::Duration::from_millis(100));
            loop {
                tokio::select! {
                    _=stopped.cancelled()=>break,
                    completed=connections.join_next(),if !connections.is_empty()=>{let _=completed;},
                    _=collect.tick()=>{let mut tasks=upgrades.tasks.lock().await;while tasks.try_join_next().is_some(){}},
                    accepted=listener.accept()=>{
                        let Ok((socket,_))=accepted else {break;};
                        let Ok(permit)=limit.clone().try_acquire_owned() else {continue;};
                        let config=config.clone();let client=client.clone();let stop=stopped.clone();let upgrades=upgrades.clone();
                        connections.spawn(async move {
                            let _permit=permit;
                            let service=service_fn(move |request| forward(request,config.clone(),client.clone(),upgrades.clone(),stop.clone()));
                            let _=http1::Builder::new().max_buf_size(64*1024).header_read_timeout(std::time::Duration::from_secs(5)).timer(hyper_util::rt::TokioTimer::new()).serve_connection(TokioIo::new(socket),service).with_upgrades().await;
                        });
                    }
                }
            }
            connections.abort_all();
            while connections.join_next().await.is_some() {}
            let mut upgrades = upgrades.tasks.lock().await;
            upgrades.abort_all();
            while upgrades.join_next().await.is_some() {}
        });
        Ok(Self {
            address,
            stop,
            worker: Mutex::new(Some(worker)),
        })
    }
    pub fn address(&self) -> SocketAddr {
        self.address
    }
    pub async fn close(&self) {
        self.stop.cancel();
        if let Some(worker) = self.worker.lock().await.take() {
            let _ = worker.await;
        }
    }
}
fn reply(status: StatusCode) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONNECTION, "close")
        .body(
            Full::new(Bytes::new())
                .map_err(|e: Infallible| match e {})
                .boxed_unsync(),
        )
        .expect("static response")
}
async fn forward(
    mut request: Request<Incoming>,
    config: BrowserConfig,
    client: reqwest::Client,
    upgrades: Upgrades,
    stop: CancellationToken,
) -> Result<Response<Body>, Infallible> {
    if request.method() == hyper::Method::CONNECT {
        let Ok(permit) = upgrades.limit.clone().try_acquire_owned() else {
            return Ok(reply(StatusCode::SERVICE_UNAVAILABLE));
        };
        let authority = request.uri().authority().map(|a| a.as_str()).unwrap_or("");
        let Ok(url) = url::Url::parse(&format!("https://{authority}/")) else {
            return Ok(reply(StatusCode::FORBIDDEN));
        };
        if request.uri().path_and_query().is_some()
            || !config.allowed(url.as_str())
            || authority.len() > 2048
        {
            return Ok(reply(StatusCode::FORBIDDEN));
        }
        let Some(host) = url.host_str().map(str::to_owned) else {
            return Ok(reply(StatusCode::FORBIDDEN));
        };
        let port = url.port_or_known_default().unwrap_or(443);
        let connect = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            TcpStream::connect((host.as_str(), port)),
        );
        let remote = tokio::select! {_=stop.cancelled()=>return Ok(reply(StatusCode::BAD_GATEWAY)),result=connect=>result};
        let Ok(Ok(mut remote)) = remote else {
            return Ok(reply(StatusCode::BAD_GATEWAY));
        };
        let upgrade = hyper::upgrade::on(&mut request);
        upgrades.tasks.lock().await.spawn(async move {
            let _permit=permit;if let Ok(upgraded)=upgrade.await {let mut browser=TokioIo::new(upgraded);tokio::select!{_=stop.cancelled()=>{},_=tokio::io::copy_bidirectional(&mut browser,&mut remote)=>{}}}});
        let mut response = reply(StatusCode::OK);
        response.headers_mut().remove(header::CONNECTION);
        return Ok(response);
    }
    let Ok(mut url) = url::Url::parse(&request.uri().to_string()) else {
        return Ok(reply(StatusCode::FORBIDDEN));
    };
    if url.scheme() == "ws" {
        let _ = url.set_scheme("http");
    }
    if url.scheme() != "http" || !config.allowed(url.as_str()) {
        return Ok(reply(StatusCode::FORBIDDEN));
    }
    let websocket = request
        .headers()
        .get(header::UPGRADE)
        .is_some_and(|value| value.as_bytes().eq_ignore_ascii_case(b"websocket"));
    let incoming_upgrade = if websocket {
        Some(hyper::upgrade::on(&mut request))
    } else {
        None
    };
    let (parts, body) = request.into_parts();
    let mut headers = parts.headers;
    // reqwest creates framing and Host for the validated URL; renderer supplied proxy
    // and hop-by-hop routing headers never determine the outgoing destination.
    for name in [header::HOST, header::TRANSFER_ENCODING, header::CONNECTION] {
        headers.remove(name);
    }
    headers.remove("proxy-authorization");
    headers.remove("proxy-connection");
    headers.remove("proxy-authenticate");
    if websocket {
        headers.insert(
            header::CONNECTION,
            header::HeaderValue::from_static("Upgrade"),
        );
    }
    let outgoing = client
        .request(parts.method, url)
        .headers(headers)
        .body(reqwest::Body::wrap_stream(BodyDataStream::new(body)))
        .send();
    let remote = tokio::select! {_=stop.cancelled()=>return Ok(reply(StatusCode::BAD_GATEWAY)),response=outgoing=>response};
    let Ok(remote) = remote else {
        return Ok(reply(StatusCode::BAD_GATEWAY));
    };
    let status = remote.status();
    let mut headers = remote.headers().clone();
    headers.remove(header::TRANSFER_ENCODING);
    if let Some(incoming_upgrade) =
        incoming_upgrade.filter(|_| status == StatusCode::SWITCHING_PROTOCOLS)
    {
        let Ok(permit) = upgrades.limit.clone().try_acquire_owned() else {
            return Ok(reply(StatusCode::SERVICE_UNAVAILABLE));
        };
        upgrades.tasks.lock().await.spawn(async move {
            let _permit=permit;if let (Ok(browser),Ok(mut remote))=tokio::join!(incoming_upgrade,remote.upgrade()){let mut browser=TokioIo::new(browser);tokio::select!{_=stop.cancelled()=>{},_=tokio::io::copy_bidirectional(&mut browser,&mut remote)=>{}}}});
        let mut response = reply(status);
        *response.headers_mut() = headers;
        return Ok(response);
    }
    let body = StreamBody::new(
        remote
            .bytes_stream()
            .map(|chunk| chunk.map(Frame::data).map_err(|e| Box::new(e) as Error)),
    )
    .boxed_unsync();
    let mut response = Response::new(body);
    *response.status_mut() = status;
    *response.headers_mut() = headers;
    Ok(response)
}
