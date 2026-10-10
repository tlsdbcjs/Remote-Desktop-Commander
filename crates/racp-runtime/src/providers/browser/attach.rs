//! Opt-in loopback CDP: attach selected targets without owning their browser process.
use super::{
    cdp::{self, Cdp},
    operations::bounded,
    session::{Browser, Session, State},
};
use racp_contract::{new_id, timestamp, RacpError};
use racp_core::{atomic_write, private_dir};
use serde_json::{json, Value};
use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;

impl Browser {
    async fn remote_peer(&self, request: &Value) -> Result<(String, Cdp), RacpError> {
        if !self.config.cdp_enabled {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        self.guards.root(
            request["context"]["workspace_id"]
                .as_str()
                .unwrap_or("default"),
        )?;
        let endpoint =
            cdp::discover(request["payload"]["endpoint_url"].as_str().unwrap_or("")).await?;
        let peer = Cdp::connect(&endpoint).await?;
        Ok((endpoint, peer))
    }
    pub(super) async fn targets(
        &self,
        request: &Value,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        let (_, cdp) = self.remote_peer(request).await?;
        let result = cdp.call("Target.getTargets", json!({}), None, cancel).await;
        cdp.close().await;
        let result = result?;
        let pages = result["targetInfos"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|p| p["type"] == "page")
            .collect::<Vec<_>>();
        let targets = pages.iter().take(64).map(|p|json!({"target_id":bounded(p["targetId"].as_str().unwrap_or(""),128),"title":bounded(p["title"].as_str().unwrap_or(""),256),"url":bounded(p["url"].as_str().unwrap_or(""),256)})).collect::<Vec<_>>();
        Ok(json!({"targets":targets,"truncated":pages.len()>64,"trust":"untrusted_browser_data"}))
    }
    pub(super) async fn attach(
        &self,
        request: &Value,
        cancel: &CancellationToken,
        effect: &std::sync::atomic::AtomicBool,
    ) -> Result<Value, RacpError> {
        let _opening = tokio::select! {_ =cancel.cancelled()=>return Err(RacpError::new("CANCELLED")), opening=self.opening.lock()=>opening};
        {
            let sessions = self
                .sessions
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            if sessions.len() >= 12
                || sessions
                    .values()
                    .filter(|s| s.handle()["state"] != "CLOSED")
                    .count()
                    >= 4
            {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
        }
        let (endpoint, cdp) = self.remote_peer(request).await?;
        let borrowed = request["payload"]["context_mode"] == "existing";
        let selected = request["payload"]["page_target_ids"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str().map(str::to_owned))
            .collect::<Vec<_>>();
        if borrowed {
            let live = cdp
                .call("Target.getTargets", json!({}), None, cancel)
                .await?;
            let pages = live["targetInfos"]
                .as_array()
                .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?;
            if selected.iter().any(|id| {
                !pages
                    .iter()
                    .any(|p| p["type"] == "page" && p["targetId"] == *id)
            }) {
                cdp.close().await;
                return Err(RacpError::new("HANDLE_EXPIRED"));
            }
        }
        // Even borrowed sessions create new pages only inside a disposable owned context.
        let backend_version = super::operations::bounded(
            cdp.call("Browser.getVersion", json!({}), None, cancel)
                .await?["product"]
                .as_str()
                .unwrap_or(""),
            256,
        );
        let proxy = super::PolicyProxy::bind(self.config.clone()).await?;
        effect.store(true, std::sync::atomic::Ordering::SeqCst);
        let context = cdp
            .call(
                "Target.createBrowserContext",
                json!({"disposeOnDetach":true,"proxyServer":format!("http://{}",proxy.address()),"proxyBypassList":"<-loopback>"}),
                None,
                cancel,
            )
            .await?["browserContextId"]
            .as_str()
            .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?
            .to_owned();
        let id = new_id("browser");
        let profile = self.root.join(&id);
        private_dir(&profile)?;
        let allow_termination = request["payload"]["allow_page_termination"] == true;
        let marker = json!({"version":1,"agent_boot_id":self.boot,"device_id":self.device,"endpoint":endpoint,"context_id":context,"target_ids":if allow_termination {selected.clone()}else{vec![]}});
        if let Err(e) = atomic_write(
            &profile.join("ownership.json"),
            &serde_json::to_vec(&marker)?,
            false,
        ) {
            cdp.close().await;
            let _ = std::fs::remove_dir_all(&profile);
            return Err(e);
        }
        let handle = json!({"id":id,"type":"browser","device_id":self.device,"owner":request["context"]["principal_id"],"agent_boot_id":self.boot,"workspace_id":request["context"]["workspace_id"],"provider_instance_id":self.instance,"resource_revision":"1","created_at":timestamp(),"last_access_at":timestamp(),"expires_at":(chrono::Utc::now()+chrono::Duration::hours(1)).to_rfc3339_opts(chrono::SecondsFormat::Micros,true),"state":"CREATING","availability":"unavailable","ownership":if borrowed{"borrowed"}else{"racp_owned"}});
        let session = Arc::new(Session {
            events: self.outbox.clone(),
            siblings: Arc::downgrade(&self.sessions),
            opening_operation: request["operation_id"].as_str().unwrap_or("").into(),
            backend_version,
            remote: Some(endpoint),
            borrowed,
            allow_termination,
            selected,
            cdp,
            context,
            state: Mutex::new(State {
                history: vec![],
                uploads: 0,
                download: None,
                handle,
                pages: Default::default(),
                expires: Instant::now() + Duration::from_secs(3600),
            }),
            process: Mutex::new(None),
            protected: Mutex::new(None),
            proxy,
            profile,
            width: request["payload"]["width"].as_u64().unwrap_or(1280),
            height: request["payload"]["height"].as_u64().unwrap_or(720),
            serial: Default::default(),
            closing: Default::default(),
            notify: Default::default(),
            stop: CancellationToken::new(),
            worker: Default::default(),
        });
        self.sessions
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .insert(id.clone(), session.clone());
        session.start(self.config.clone()).await;
        let result = async {
            // Global auto-attach would pause or alter unrelated user targets. Attach explicitly.
            session.watch_downloads(cancel).await?;
            session.cdp.call("Target.setDiscoverTargets",json!({"discover":true}),None,cancel).await?;
            session.cdp.call("Target.setAutoAttach",json!({"autoAttach":false,"waitForDebuggerOnStart":true,"flatten":true}),None,cancel).await?;
            if borrowed {
                for target in &session.selected { session.cdp.call("Target.attachToTarget",json!({"targetId":target,"flatten":true}),None,cancel).await?; }
                loop {
                    let notified = session.notify.notified();
                    let ready = session.state.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?.pages.values().filter(|p|p.frames.get(&p.main).is_some_and(|f|f.context.is_some())).count()==session.selected.len();
                    if ready { break; }
                    tokio::select!{_=cancel.cancelled()=>return Err(RacpError::new("CANCELLED")),_=session.stop.cancelled()=>return Err(RacpError::new("BROWSER_ERROR")),_=notified=>{}}
                }
            } else { session.new_page(cancel).await?; }
            {let mut state=session.state.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?; state.handle["state"]=json!("ACTIVE");state.handle["availability"]=json!("available");}
            session.emit("inventory","active");
            Ok(session.opened())
        }.await;
        if result.is_err() {
            session.close().await?;
        }
        result
    }
}
impl Session {
    pub(super) async fn cleanup_remote(&self) -> Result<(), RacpError> {
        // A disconnected peer may be replaced only with the already validated same endpoint.
        let replacement;
        let peer = if self.cdp.is_closed() {
            replacement = Cdp::connect(
                self.remote
                    .as_ref()
                    .ok_or_else(|| RacpError::new("CLEANUP_FAILED"))?,
            )
            .await?;
            &replacement
        } else {
            &self.cdp
        };
        let targets = if self.borrowed && self.allow_termination {
            self.selected.clone()
        } else {
            vec![]
        };
        let result = tokio::time::timeout(
            Duration::from_secs(5),
            super::recovery::cleanup_scope(peer, &self.context, &targets),
        )
        .await
        .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        if self.cdp.is_closed() {
            peer.close().await;
        }
        result.map_err(|_| RacpError::new("CLEANUP_FAILED"))
    }
}
