use super::{
    containment::{self, CommandSpec, OwnedProcess},
    cursor::Cursor,
    Provider,
};
use futures_util::future::BoxFuture;
use racp_contract::{new_id, timestamp, RacpError};
use racp_core::{error_value, AgentSettings, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
struct Managed {
    process: Option<OwnedProcess>,
    handle: Value,
    expires: Instant,
}
impl Managed {
    fn tick(&mut self) -> Result<(), RacpError> {
        if let Some(process) = &mut self.process {
            let mut buffer = [0; 8192];
            for pipe in [&mut process.stdout, &mut process.stderr] {
                for _ in 0..32 {
                    match pipe.read_available(&mut buffer) {
                        Ok(Some(n)) if n > 0 => {}
                        _ => break,
                    }
                }
            }
            if let Some(code) = process.poll()? {
                self.handle["exit_code"] = json!(code);
                self.handle["state"] = json!("CLOSED");
                self.handle["availability"] = json!("exited");
                self.handle["resource_revision"] = json!("2");
                drop(self.process.take());
            }
        }
        if Instant::now() >= self.expires && self.process.is_some() {
            self.close()?;
            self.handle["state"] = json!("EXPIRED");
        }
        Ok(())
    }
    fn close(&mut self) -> Result<(), RacpError> {
        if let Some(mut process) = self.process.take() {
            process.kill_tree()?;
            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                if let Some(code) = process.poll()? {
                    self.handle["exit_code"] = json!(code);
                    if process.tree_empty()? {
                        break;
                    }
                }
                if Instant::now() >= deadline {
                    return Err(RacpError::new("CLEANUP_FAILED"));
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            drop(process);
        }
        self.handle["state"] = json!("CLOSED");
        self.handle["availability"] = json!("exited");
        self.handle["resource_revision"] = json!("2");
        Ok(())
    }
    fn identity(&self, pid: u32, birth: f64) -> bool {
        self.handle["pid"].as_u64() == Some(pid as u64)
            && self.handle["create_time"]
                .as_f64()
                .is_some_and(|v| (v - birth).abs() <= 0.000001)
    }
}
struct Snapshot {
    expires: Instant,
    items: Vec<Value>,
}
#[derive(Clone)]
pub struct Processes {
    guards: Workspaces,
    managed: Arc<Mutex<BTreeMap<String, Managed>>>,
    snapshots: Arc<Mutex<BTreeMap<String, Snapshot>>>,
    cursor: Cursor,
    instance: String,
    boot: String,
    device: String,
}
impl Processes {
    pub fn new(settings: &AgentSettings, boot: &str) -> Result<Self, RacpError> {
        let this = Self {
            guards: Workspaces::new(&settings.workspace, &settings.allowed_workspaces)?,
            managed: Arc::new(Mutex::new(BTreeMap::new())),
            snapshots: Arc::new(Mutex::new(BTreeMap::new())),
            cursor: Cursor::default(),
            instance: new_id("provider"),
            boot: boot.into(),
            device: settings.device_id.clone(),
        };
        let weak = Arc::downgrade(&this.managed);
        tokio::spawn(async move {
            loop {
                let Some(managed) = weak.upgrade() else { break };
                let _ = tokio::task::spawn_blocking(move || {
                    if let Ok(mut map) = managed.lock() {
                        for child in map.values_mut() {
                            let _ = child.tick();
                        }
                        map.retain(|_, m| m.expires > Instant::now());
                    }
                })
                .await;
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        });
        Ok(this)
    }
    fn describe(&self, process: &sysinfo::Process, users: &sysinfo::Users) -> Value {
        let pid = process.pid().as_u32();
        let birth = crate::identity::process_created(pid).unwrap_or(process.start_time() as f64);
        json!({"pid":pid,"ppid":process.parent().map(|p|p.as_u32()),"name":process.name().to_string_lossy(),"exe":process.exe(),"cmdline":process.cmd().iter().map(|s|s.to_string_lossy()).collect::<Vec<_>>(),"username":process.user_id().and_then(|id|users.get_user_by_id(id)).map(|u|u.name()),"status":process.status().to_string().to_ascii_lowercase(),"create_time":birth,"agent_boot_id":self.boot})
    }
    fn list(&self, payload: &Value, principal: &str) -> Result<Value, RacpError> {
        let limit = payload["limit"].as_u64().unwrap_or(100) as usize;
        let scope = format!("process.list:{principal}:{limit}");
        let mut snapshots = self
            .snapshots
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        snapshots.retain(|_, s| s.expires > Instant::now());
        let (id, offset) = if let Some(cursor) = payload["cursor"].as_str() {
            snapshots
                .keys()
                .find_map(|id| {
                    self.cursor
                        .decode(Some(cursor), &scope, id)
                        .ok()
                        .map(|offset| (id.clone(), offset))
                })
                .ok_or_else(|| RacpError::new("CURSOR_EXPIRED"))?
        } else {
            let system = sysinfo::System::new_all();
            let users = sysinfo::Users::new_with_refreshed_list();
            let mut items: Vec<_> = system
                .processes()
                .values()
                .map(|p| self.describe(p, &users))
                .collect();
            items.sort_by_key(|p| p["pid"].as_u64());
            if snapshots.len() >= 16 {
                if let Some(id) = snapshots.keys().next().cloned() {
                    snapshots.remove(&id);
                }
            }
            let id = new_id("snapshot");
            snapshots.insert(
                id.clone(),
                Snapshot {
                    expires: Instant::now() + Duration::from_secs(300),
                    items,
                },
            );
            (id, 0)
        };
        let items = &snapshots[&id].items;
        if offset > items.len() {
            return Err(RacpError::new("CURSOR_EXPIRED"));
        }
        let end = (offset + limit).min(items.len());
        Ok(
            json!({"items":items[offset..end],"next_cursor":if end<items.len(){Some(self.cursor.encode(end,&scope,&id)?)}else{None},"consistency":"snapshot_observation","snapshot_id":id}),
        )
    }
    fn spawn(&self, request: &Value, cancel: &CancellationToken) -> Result<Value, RacpError> {
        let p = &request["payload"];
        let workspace = request["context"]["workspace_id"]
            .as_str()
            .unwrap_or("default");
        let principal = request["context"]["principal_id"].as_str().unwrap_or("");
        let argv = containment::argv(p)?;
        let environment = containment::environment(&p["env"])?;
        let path = self
            .guards
            .path(workspace, p["cwd"].as_str().unwrap_or("."))?;
        let cwd = self.guards.directory(workspace, &path)?;
        let mut managed = self
            .managed
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        if managed.len() >= 64 || managed.values().filter(|m| m.process.is_some()).count() >= 32 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        let mut process = OwnedProcess::spawn(CommandSpec {
            argv,
            environment,
            cwd,
        })?;
        let pid = process.pid();
        let birth = crate::identity::process_created(pid).unwrap_or(0.0);
        if cancel.is_cancelled() {
            process.kill_tree()?;
            return Err(RacpError::new("CANCELLED"));
        }
        let id = new_id("proc");
        let now = timestamp();
        let handle = json!({"id":id,"type":"interactive-process","device_id":self.device,"owner":principal,"agent_boot_id":self.boot,"workspace_id":workspace,"provider_instance_id":self.instance,"resource_revision":"1","created_at":now,"last_access_at":now,"expires_at":(chrono::Utc::now()+chrono::Duration::hours(1)).to_rfc3339_opts(chrono::SecondsFormat::Micros,true),"state":"ACTIVE","availability":"available","pid":pid,"create_time":birth,"exit_code":null,"output":"discard"});
        let mut child = Managed {
            process: Some(process),
            handle,
            expires: Instant::now() + Duration::from_secs(3600),
        };
        child.tick()?;
        let result =
            json!({"handle":child.handle,"pid":pid,"create_time":birth,"agent_boot_id":self.boot});
        managed.insert(id, child);
        Ok(result)
    }
    fn check_identity(&self, p: &Value) -> Result<(u32, f64), RacpError> {
        if p["agent_boot_id"] != self.boot {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let pid = p["pid"].as_u64().unwrap_or(0) as u32;
        let expected = p["create_time"].as_f64().unwrap_or(0.0);
        let observed = crate::identity::process_created(pid).map_err(|e| {
            if e.code.0 == "PERMISSION_DENIED" {
                e
            } else {
                RacpError::new("PROCESS_NOT_FOUND")
            }
        })?;
        if (observed - expected).abs() > 0.000001 {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        Ok((pid, expected))
    }
    fn protected(system: &sysinfo::System, pid: u32) -> bool {
        let mut parent = Some(sysinfo::Pid::from_u32(std::process::id()));
        let mut ancestors = std::collections::BTreeSet::new();
        for _ in 0..100 {
            let Some(p) = parent else { break };
            if !ancestors.insert(p.as_u32()) {
                break;
            }
            parent = system.process(p).and_then(|p| p.parent());
        }
        if pid <= 1 || ancestors.contains(&pid) || crate::identity::provider_protected(system, pid)
        {
            return true;
        }
        let Some(p) = system.process(sysinfo::Pid::from_u32(pid)) else {
            return true;
        };
        [
            "system",
            "registry",
            "smss.exe",
            "csrss.exe",
            "wininit.exe",
            "services.exe",
            "lsass.exe",
            "winlogon.exe",
        ]
        .contains(&p.name().to_string_lossy().to_ascii_lowercase().as_str())
            || p.cmd().iter().any(|arg| {
                [
                    "racp_gateway",
                    "racp-gateway",
                    "racp_agent.",
                    "racp-agent",
                    "racp_session_broker",
                    "racp-session-broker",
                    "racp-login-broker",
                ]
                .iter()
                .any(|name| arg.to_string_lossy().to_ascii_lowercase().contains(name))
            })
    }
    fn run(&self, request: Value, cancel: CancellationToken) -> Result<Value, RacpError> {
        let operation = request["operation"].as_str().unwrap_or("");
        let p = &request["payload"];
        let principal = request["context"]["principal_id"].as_str().unwrap_or("");
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if operation == "process.spawn" {
            return self.spawn(&request, &cancel);
        }
        if operation == "process.list" {
            return self.list(p, principal);
        }
        let pid = p["pid"].as_u64().unwrap_or(0) as u32;
        if ["process.inspect", "process.tree"].contains(&operation) {
            let system = sysinfo::System::new_all();
            let users = sysinfo::Users::new_with_refreshed_list();
            let target = system
                .process(sysinfo::Pid::from_u32(pid))
                .ok_or_else(|| RacpError::new("PROCESS_NOT_FOUND"))?;
            let mut item = self.describe(target, &users);
            if operation == "process.inspect" {
                if let Some(child) = self
                    .managed
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                    .values()
                    .find(|m| {
                        m.handle["pid"] == pid
                            && m.handle["owner"] == principal
                            && m.handle["create_time"] == item["create_time"]
                    })
                {
                    item["handle"] = child.handle.clone();
                }
                return Ok(item);
            }
            let mut selected = std::collections::BTreeSet::from([pid]);
            let mut items = vec![item];
            loop {
                let mut count = 0;
                for child in system.processes().values() {
                    if !selected.contains(&child.pid().as_u32())
                        && child
                            .parent()
                            .is_some_and(|p| selected.contains(&p.as_u32()))
                    {
                        selected.insert(child.pid().as_u32());
                        items.push(self.describe(child, &users));
                        count += 1;
                        if items.len() > 1000 {
                            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                        }
                    }
                }
                if count == 0 {
                    break;
                }
            }
            return Ok(json!({"items":items}));
        }
        let (pid, birth) = self.check_identity(p)?;
        if operation == "process.wait" {
            let deadline = Instant::now()
                + Duration::from_millis(request["remaining_timeout_ms"].as_u64().unwrap_or(1));
            loop {
                if cancel.is_cancelled() {
                    return Err(RacpError::new("CANCELLED"));
                }
                if Instant::now() >= deadline {
                    return Err(RacpError::new("TIMEOUT"));
                }
                let mut managed = self
                    .managed
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                if let Some(child) = managed.values_mut().find(|m| m.identity(pid, birth)) {
                    if child.handle["owner"] != principal {
                        return Err(RacpError::new("PERMISSION_DENIED"));
                    }
                    child.tick()?;
                    if child.process.is_none() {
                        return Ok(
                            json!({"pid":pid,"exited":true,"exit_code":child.handle["exit_code"]}),
                        );
                    }
                } else if crate::identity::process_created(pid).is_err() {
                    return Ok(json!({"pid":pid,"exited":true,"exit_code":null}));
                } else {
                    self.check_identity(p)?;
                }
                drop(managed);
                std::thread::sleep(Duration::from_millis(20));
            }
        }
        if operation != "process.terminate" {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let system = sysinfo::System::new_all();
        if Self::protected(&system, pid) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let force = p["force"].as_bool().unwrap_or(false);
        let mut managed = self
            .managed
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        if let Some(child) = managed.values_mut().find(|m| m.identity(pid, birth)) {
            if child.handle["owner"] != principal {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            if force {
                child.close()?;
                return Ok(
                    json!({"pid":pid,"exited":true,"cleanup_status":"complete","method":"owned_tree_kill"}),
                );
            }
        }
        drop(managed);
        crate::identity::signal_process(pid, birth, force)?;
        Ok(
            json!({"pid":pid,"exited":false,"signal_sent":true,"force":force,"next_action":"process_wait"}),
        )
    }
}
impl Provider for Processes {
    fn capabilities(&self) -> Vec<Value> {
        vec![
            json!({"name":"process","version":"1.0.0","operations":["process.list","process.inspect","process.tree","process.spawn","process.wait","process.terminate"],"installed":true,"supported":true,"enabled":true,"healthy":true,"unavailable_reason":null,"attributes":{"output":"discard","max_active":32,"max_history":64}}),
        ]
    }
    fn inventory(&self) -> Vec<Value> {
        self.managed
            .lock()
            .map(|map| map.values().map(|m| m.handle.clone()).collect())
            .unwrap_or_default()
    }
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        let this = self.clone();
        let waiting = request["operation"] == "process.wait";
        Box::pin(async move {
            let outcome = tokio::task::spawn_blocking(move || this.run(request, cancel))
                .await
                .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
            Ok(match outcome {
                Ok(result) => json!({"state":"SUCCEEDED","result":result,"error":null}),
                Err(e) => {
                    let mut error = error_value(
                        e.code.0,
                        "process observation or execution rejected",
                        "not_started",
                    );
                    if waiting {
                        error["details"]["target_terminated"] = json!(false);
                    }
                    json!({"state":match e.code.0{"TIMEOUT"=>"TIMED_OUT","CANCELLED"=>"CANCELLED",_=>"FAILED"},"result":null,"error":error})
                }
            })
        })
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        let managed = self.managed.clone();
        Box::pin(async move {
            tokio::task::spawn_blocking(move || {
                let mut map = managed
                    .lock()
                    .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
                for child in map.values_mut() {
                    if let Some(p) = &mut child.process {
                        p.kill_tree()?;
                    }
                }
                for child in map.values_mut() {
                    child.close()?;
                }
                Ok(())
            })
            .await
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
        })
    }
}
