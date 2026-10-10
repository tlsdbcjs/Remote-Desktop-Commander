//! Scoped analysis/debugger handles over explicitly approved native plugins.
use super::plugin::{decode, Approved, Supervisor};
use crate::providers::{cursor::Cursor, Provider};
use futures_util::future::BoxFuture;
use racp_contract::{digest, new_id, timestamp, validate_schema, RacpError};
use racp_core::{private_dir, read_bounded, secure_create_file, AgentSettings, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
struct Resource {
    handle: Value,
    backend: String,
    instance: String,
    private_id: String,
    root: PathBuf,
    expires: Instant,
    active: bool,
    revision: u64,
    page_revision: u64,
    cursors: BTreeMap<usize, String>,
    cursor_sequence: usize,
    stops: VecDeque<Value>,
}
struct State {
    backends: BTreeMap<String, Supervisor>,
    resources: BTreeMap<String, Resource>,
    errors: Vec<&'static str>,
}
#[derive(Clone)]
pub struct Reversing {
    state: Arc<Mutex<State>>,
    guards: Workspaces,
    root: PathBuf,
    spool: PathBuf,
    boot: String,
    device: String,
    cursor: Cursor,
}
fn budget(deadline: Instant, cancel: &CancellationToken) -> Result<(), RacpError> {
    if cancel.is_cancelled() {
        return Err(RacpError::new("CANCELLED"));
    }
    if Instant::now() >= deadline {
        return Err(RacpError::new("TIMEOUT"));
    }
    Ok(())
}
impl Reversing {
    pub fn new(settings: &AgentSettings, boot: &str) -> Result<Self, RacpError> {
        let root = settings.data_dir.join("reversing");
        private_dir(&root)?;
        let spool = settings.data_dir.join("spool");
        private_dir(&spool)?;
        let mut state = State {
            backends: BTreeMap::new(),
            resources: BTreeMap::new(),
            errors: vec![],
        };
        let config = settings.data_dir.join("plugins.json");
        if config.try_exists()? {
            let loaded = (|| {
                let raw = read_bounded(&config, 65536, true)?;
                let config = decode(&raw, 65536)?;
                let schema: Value = serde_json::from_str(include_str!(
                    "../../../../../docs/protocol/plugin-installations-v1.schema.json"
                ))
                .expect("installation schema");
                validate_schema(&schema, &config)?;
                for installed in config["plugins"].as_array().into_iter().flatten() {
                    let path = Path::new(installed["manifest"].as_str().unwrap());
                    let hash = installed["sha256"].as_str().unwrap();
                    let permissions: BTreeSet<_> = installed["permissions"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|v| v.as_str().unwrap().into())
                        .collect();
                    let approved = Approved::load(path, hash, &permissions)?;
                    let name = approved.manifest["name"].as_str().unwrap().to_owned();
                    if state.backends.contains_key(&name) {
                        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                    }
                    let mut backend = Supervisor::new(approved);
                    if backend.health(&CancellationToken::new()).is_err() {
                        state.errors.push("PLUGIN_START_FAILED");
                    }
                    state.backends.insert(name, backend);
                }
                Ok::<(), RacpError>(())
            })();
            if loaded.is_err() {
                state.errors.push("PLUGIN_CONFIGURATION_INVALID");
            }
        }
        let provider = Self {
            state: Arc::new(Mutex::new(state)),
            guards: Workspaces::new(&settings.workspace, &settings.allowed_workspaces)?,
            root,
            spool,
            boot: boot.into(),
            device: settings.device_id.clone(),
            cursor: Cursor::default(),
        };
        let weak=Arc::downgrade(&provider.state);
        let guards=provider.guards.clone();let root=provider.root.clone();let spool=provider.spool.clone();let device=provider.device.clone();let boot=provider.boot.clone();
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_secs(1));
            let Some(state)=weak.upgrade() else {break;};
            let monitor=Self{state,guards:guards.clone(),root:root.clone(),spool:spool.clone(),device:device.clone(),boot:boot.clone(),cursor:Cursor::default()};
            if let Ok(mut state)=monitor.state.try_lock(){let _=monitor.retire(&mut state);};
        });
        Ok(provider)
    }
    fn handle<'a>(
        &self,
        state: &'a mut State,
        id: &str,
        r: &Value,
        closing: bool,
    ) -> Result<&'a mut Resource, RacpError> {
        let resource = state
            .resources
            .get_mut(id)
            .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
        if resource.handle["workspace_id"] != r["context"]["workspace_id"]
            || resource.handle["owner"] != r["context"]["principal_id"]
            || resource.handle["device_id"] != r["device_id"]
            || r["device_id"] != self.device
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if r["agent_boot_id"] != self.boot || resource.handle["agent_boot_id"] != self.boot {
            return Err(RacpError::new("HANDLE_EXPIRED"));
        }
        if !closing
            && (!resource.active
                || resource.expires <= Instant::now()
                || state
                    .backends
                    .get(&resource.backend)
                    .is_none_or(|b| !b.ready || b.instance != resource.instance))
        {
            return Err(RacpError::new("HANDLE_EXPIRED"));
        }
        Ok(resource)
    }
    fn events(state: &mut State) {
        for (name, backend) in &mut state.backends {
            while let Some(event) = backend.events.pop_front() {
                for resource in state.resources.values_mut().filter(|r| {
                    r.backend == *name
                        && r.instance == backend.instance
                        && r.private_id == event["data"]["resource_id"].as_str().unwrap_or("")
                        && r.active
                }) {
                    if let Some(next) = event["data"]["stop_sequence"]
                        .as_str()
                        .and_then(|s| s.parse::<u64>().ok())
                    {
                        let previous = resource.handle["stop_sequence"]
                            .as_str()
                            .and_then(|s| s.parse::<u64>().ok())
                            .unwrap_or(0);
                        if next < previous {
                            resource.active = false;
                            resource.handle["state"] = json!("EXPIRED");
                            continue;
                        }
                    }
                    for key in [
                        "debugger_state",
                        "stop_sequence",
                        "stop_reason",
                        "analysis_state",
                    ] {
                        if let Some(value) = event["data"].get(key) {
                            resource.handle[key] = value.clone();
                        }
                    }
                    resource.revision += 1;
                    resource.handle["resource_revision"] = json!(resource.revision.to_string());
                    if matches!(
                        event["kind"].as_str(),
                        Some("debugger.stopped" | "debugger.exited")
                    ) {
                        resource.stops.push_back(json!({"sequence":resource.handle["stop_sequence"],"reason":resource.handle["stop_reason"],"state":resource.handle["debugger_state"]}));
                        while resource.stops.len() > 64 {
                            resource.stops.pop_front();
                        }
                    }
                }
            }
            if !backend.ready {
                for resource in state
                    .resources
                    .values_mut()
                    .filter(|r| r.backend == *name && r.active)
                {
                    resource.active = false;
                    resource.handle["state"] = json!("EXPIRED");
                    resource.handle["debugger_state"] = json!("FAILED");
                }
            }
        }
    }
    fn copy_target(
        &self,
        r: &Value,
        id: &str,
        source: &str,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<PathBuf, RacpError> {
        let workspace = r["context"]["workspace_id"].as_str().unwrap_or("default");
        let path = self.guards.path(workspace, source)?;
        let parent = self.guards.parent(workspace, &path)?;
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let observed = parent.require_file(name)?;
        if observed.directory || observed.size > 1024 * 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        if self.storage()?.saturating_add(observed.size) > 10 * 1024 * 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let mut file = parent.open_read(name)?;
        if racp_core::FileInfo::from_file(&file)?.revision() != observed.revision() {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let directory = self.root.join(id);
        private_dir(&directory)?;
        let target = directory.join(name);
        let mut copied = secure_create_file(&target)?;
        let result = (|| {
            let mut buffer = [0u8; 262144];
            let mut size = 0u64;
            use sha2::{Digest, Sha256};
            let mut hash = Sha256::new();
            loop {
                budget(deadline, cancel)?;
                let n = file.read(&mut buffer)?;
                if n == 0 {
                    break;
                }
                size += n as u64;
                if size > 1024 * 1024 * 1024 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
                copied.write_all(&buffer[..n])?;
                hash.update(&buffer[..n]);
            }
            copied.sync_all()?;
            if size != observed.size
                || racp_core::FileInfo::from_file(&file)?.revision() != observed.revision()
                || parent.require_file(name)?.revision() != observed.revision()
            {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            let hash = format!("{:x}", hash.finalize());
            if r["payload"]["expected_sha256"]
                .as_str()
                .is_some_and(|s| s != hash)
            {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            Ok(())
        })();
        if result.is_err() {
            drop(copied);
            let _ = std::fs::remove_file(&target);
        }
        result?;
        Ok(target)
    }

    fn storage(&self) -> Result<u64, RacpError> {
        let guards = Workspaces::new(&self.root, &[])?;
        let mut pending = vec![(self.root.clone(), 0usize)];
        let mut total = 0u64;
        let mut entries = 0;
        while let Some((path, depth)) = pending.pop() {
            if depth > 64 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let directory = guards.directory("default", &path)?;
            for name in directory.names()? {
                entries += 1;
                if entries > 20000 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
                let info = directory
                    .info(&name)?
                    .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?;
                if info.link {
                    return Err(RacpError::new("PATH_ACCESS_DENIED"));
                }
                if info.directory {
                    pending.push((path.join(name), depth + 1));
                } else {
                    total = total
                        .checked_add(info.size)
                        .filter(|n| *n <= 10 * 1024 * 1024 * 1024)
                        .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
                }
            }
        }
        Ok(total)
    }
    fn remove_resource(&self, path: &Path) -> Result<(), RacpError> {
        if path.parent() != Some(self.root.as_path()) {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        if !path.try_exists()? {
            return Ok(());
        }
        let guards = Workspaces::new(&self.root, &[])?;
        let mut stack = vec![(path.to_path_buf(), false, 0usize)];
        let mut entries = 0;
        while let Some((path, visited, depth)) = stack.pop() {
            if depth > 64 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            if visited {
                let parent = guards.parent("default", &path)?;
                parent.unlink(
                    path.file_name()
                        .and_then(|s| s.to_str())
                        .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?,
                    true,
                )?;
                continue;
            }
            stack.push((path.clone(), true, depth));
            let directory = guards.directory("default", &path)?;
            for name in directory.names()? {
                entries += 1;
                if entries > 20000 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
                let info = directory
                    .info(&name)?
                    .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?;
                if info.link {
                    return Err(RacpError::new("PATH_ACCESS_DENIED"));
                }
                if info.directory {
                    stack.push((path.join(name), false, depth + 1));
                } else {
                    directory.unlink(&name, false)?;
                }
            }
        }
        Ok(())
    }
    fn retire(&self, state: &mut State) -> Result<(), RacpError> {
        let expired: Vec<_> = state
            .resources
            .iter()
            .filter(|(_, r)| r.active && r.expires <= Instant::now())
            .map(|(id, r)| {
                (
                    id.clone(),
                    r.backend.clone(),
                    r.private_id.clone(),
                    r.handle["type"] == "analysis",
                )
            })
            .collect();
        for (id, backend, private, analysis) in expired {
            if let Some(backend) = state.backends.get_mut(&backend) {
                let _ = backend.request(
                    if analysis {
                        "re.close"
                    } else {
                        "debugger.close"
                    },
                    if analysis {
                        json!({"analysis_id":private})
                    } else {
                        json!({"debug_id":private})
                    },
                    Instant::now() + Duration::from_secs(2),
                    &CancellationToken::new(),
                );
            }
            let resource = state.resources.get_mut(&id).unwrap();
            resource.active = false;
            resource.handle["state"] = json!("EXPIRED");
            resource.handle["availability"] = json!("unavailable");
            resource.cursors.clear();
            self.remove_resource(&resource.root)?;
        }
        Self::events(state);
        for resource in state.resources.values().filter(|r| !r.active) {
            self.remove_resource(&resource.root)?;
        }
        while state.resources.len() > 64 {
            let Some(id) = state
                .resources
                .iter()
                .find(|(_, r)| !r.active)
                .map(|(id, _)| id.clone())
            else {
                break;
            };
            state.resources.remove(&id);
        }
        self.storage()?;
        Ok(())
    }

    fn run(
        &self,
        r: Value,
        cancel: CancellationToken,
        deadline: Instant,
    ) -> Result<Value, RacpError> {
        budget(deadline, &cancel)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        budget(deadline, &cancel)?;
        self.retire(&mut state)?;
        let op = r["operation"].as_str().unwrap_or("");
        let mut p = r["payload"].clone();
        if matches!(op, "re.backends" | "debugger.backends") {
            let category = if op.starts_with("re.") {
                "static-analysis"
            } else {
                "debugger"
            };
            let backends:Vec<_>=state.backends.values().filter(|b|b.approved.manifest["capabilities"].as_array().is_some_and(|a|a.iter().any(|v|v==category))).map(|b|json!({"name":b.approved.manifest["name"],"backend":b.approved.manifest["backend_name"],"backend_version":b.approved.manifest["backend_version"],"instance_id":b.instance,"state":if b.ready{"READY"}else{"DEGRADED"},"manifest_sha256":b.approved.hash})).collect();
            return Ok(json!({"backends":backends,"configuration_errors":state.errors}));
        }
        if matches!(op, "re.open" | "debugger.launch" | "debugger.attach") {
            if state.resources.values().filter(|r| r.active).count() >= 8 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let backend_name = p["backend"].as_str().unwrap_or("").to_owned();
            let backend = state
                .backends
                .get(&backend_name)
                .ok_or_else(|| RacpError::new("OPERATION_NOT_SUPPORTED"))?;
            if !backend.ready {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            }
            let instance = backend.instance.clone();
            let id = new_id(if op == "re.open" { "analysis" } else { "debug" });
            let _target = if op == "debugger.attach" {
                let pid = p["pid"].as_u64().unwrap_or(0) as u32;
                let birth = p["create_time"].as_f64().unwrap_or(0.0);
                if p["agent_boot_id"] != self.boot
                    || (crate::identity::process_created(pid)? - birth).abs() > 0.000001
                {
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
                let system = sysinfo::System::new_all();
                if crate::providers::Processes::protected(&system, pid) {
                    return Err(RacpError::new("PERMISSION_DENIED"));
                }
                #[cfg(windows)]
                {
                    let peer = crate::providers::desktop::native_identity::PinnedPeer::open(pid)?;
                    let actor = crate::providers::desktop::native_identity::PinnedPeer::open(
                        std::process::id(),
                    )?;
                    if peer.identity().sid != actor.identity().sid
                        || peer.identity().session != actor.identity().session
                        || peer.identity().integrity > actor.identity().integrity
                    {
                        return Err(RacpError::new("PERMISSION_DENIED"));
                    }
                    peer.alive()?;
                }
                let exe = system
                    .process(sysinfo::Pid::from_u32(pid))
                    .and_then(|p| p.exe())
                    .ok_or_else(|| RacpError::new("PRECONDITION_FAILED"))?;
                let snapshot = self.copy_target(
                    &r,
                    &id,
                    exe.to_str()
                        .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?,
                    deadline,
                    &cancel,
                )?;
                Some(snapshot)
            } else {
                let key = if op == "re.open" {
                    "path"
                } else {
                    "executable"
                };
                let target =
                    self.copy_target(&r, &id, p[key].as_str().unwrap_or(""), deadline, &cancel)?;
                p[key] = json!(target);
                Some(target)
            };
            let value = state
                .backends
                .get_mut(&backend_name)
                .unwrap()
                .request(op, p, deadline, &cancel);
            let value = match value {
                Ok(value) => value,
                Err(error) => {
                    if let Some(backend) = state.backends.get_mut(&backend_name) {
                        backend.shutdown()?;
                    }
                    self.remove_resource(&self.root.join(&id))?;
                    return Err(error);
                }
            };
            if let Some(target) = &_target {
                let actual = super::ghidra::file_hash(target, deadline, &cancel)?;
                if value["target_sha256"] != actual {
                    state.backends.get_mut(&backend_name).unwrap().shutdown()?;
                    self.remove_resource(&self.root.join(&id))?;
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
            }
            let private_id = match value["resource_id"]
                .as_str()
                .filter(|s| !s.is_empty() && s.len() <= 96)
            {
                Some(id) => id.to_owned(),
                None => {
                    state.backends.get_mut(&backend_name).unwrap().shutdown()?;
                    self.remove_resource(&self.root.join(&id))?;
                    return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                }
            };
            if op == "debugger.attach"
                && (value["pid"] != r["payload"]["pid"]
                    || value["create_time"] != r["payload"]["create_time"]
                    || crate::identity::process_created(
                        r["payload"]["pid"].as_u64().unwrap_or(0) as u32
                    )? != r["payload"]["create_time"].as_f64().unwrap_or(0.0))
            {
                state.backends.get_mut(&backend_name).unwrap().shutdown()?;
                self.remove_resource(&self.root.join(&id))?;
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            let now = timestamp();
            let mut handle = json!({"id":id,"type":if op=="re.open"{"analysis"}else{"debugger"},"device_id":self.device,"owner":r["context"]["principal_id"],"agent_boot_id":self.boot,"workspace_id":r["context"]["workspace_id"],"provider_instance_id":instance,"resource_revision":"1","created_at":now,"last_access_at":now,"expires_at":(chrono::Utc::now()+chrono::Duration::hours(1)).to_rfc3339_opts(chrono::SecondsFormat::Micros,true),"state":"ACTIVE","availability":"available","backend":backend_name,"backend_version":state.backends[&backend_name].approved.manifest["backend_version"],"ownership":if op=="debugger.attach"{"borrowed"}else{"racp_owned"}});
            for key in [
                "target_sha256",
                "architecture",
                "image_base",
                "analysis_state",
                "debugger_state",
                "pid",
                "create_time",
                "stop_sequence",
                "stop_reason",
            ] {
                if let Some(v) = value.get(key) {
                    handle[key] = v.clone();
                }
            }
            state.resources.insert(
                id.clone(),
                Resource {
                    handle: handle.clone(),
                    backend: backend_name,
                    instance,
                    private_id,
                    root: self.root.join(&id),
                    expires: Instant::now() + Duration::from_secs(3600),
                    active: true,
                    revision: 1,
                    page_revision: 1,
                    cursors: BTreeMap::new(),
                    cursor_sequence: 0,
                    stops: VecDeque::new(),
                },
            );
            return Ok(json!({"handle":handle}));
        }
        let key = if op.starts_with("re.") {
            "analysis_id"
        } else {
            "debug_id"
        };
        let id = p[key].as_str().unwrap_or("").to_owned();
        let closing = matches!(op, "re.close" | "debugger.close");
        let resource = self.handle(&mut state, &id, &r, closing)?;
        let backend_name = resource.backend.clone();
        if matches!(op, "re.keepalive" | "debugger.keepalive") {
            resource.expires = Instant::now() + Duration::from_secs(3600);
            resource.handle["expires_at"] = json!((chrono::Utc::now()
                + chrono::Duration::hours(1))
            .to_rfc3339_opts(chrono::SecondsFormat::Micros, true));
            resource.handle["last_access_at"] = json!(timestamp());
            return Ok(json!({"handle":resource.handle}));
        }
        if closing && !resource.active {
            return Ok(json!({"closed":true,"handle":resource.handle}));
        }
        let revision = resource.page_revision.to_string();
        let scope = format!(
            "{}:{}:{}:{}",
            r["context"]["principal_id"], id, op, p["action"]
        );
        if let Some(cursor) = p["cursor"].as_str() {
            let offset = self.cursor.decode(Some(cursor), &scope, &revision)?;
            p["cursor"] = json!(resource
                .cursors
                .get(&offset)
                .ok_or_else(|| RacpError::new("CURSOR_EXPIRED"))?);
        }
        p[key] = json!(resource.private_id);
        if op == "debugger.wait" {
            let after = p["after_sequence"]
                .as_str()
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(0);
            loop {
                budget(deadline, &cancel)?;
                let private = state.resources[&id].private_id.clone();
                let info = state.backends.get_mut(&backend_name).unwrap().request(
                    "debugger.info",
                    json!({"debug_id":private}),
                    deadline,
                    &cancel,
                )?;
                Self::events(&mut state);
                if info["stop_sequence"]
                    .as_str()
                    .and_then(|s| s.parse::<u64>().ok())
                    .is_some_and(|n| n > after)
                {
                    let resource = self.handle(&mut state, &id, &r, false)?;
                    for key in ["debugger_state", "stop_sequence", "stop_reason"] {
                        resource.handle[key] = info[key].clone();
                    }
                    return Ok(
                        json!({"handle":resource.handle,"events":resource.stops,"truncated":false}),
                    );
                }
                drop(state);
                std::thread::sleep(Duration::from_millis(25));
                state = self
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                self.handle(&mut state, &id, &r, false)?;
            }
        }
        let mut value = if op == "debugger.read_memory" {
            let requested = p["size_bytes"].as_u64().unwrap_or(4096);
            let text = p["address"]
                .as_str()
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let address = u64::from_str_radix(text.trim_start_matches("0x"), 16)
                .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
            address
                .checked_add(requested)
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let mut combined = String::with_capacity(requested as usize * 2);
            let mut offset = 0u64;
            while offset < requested {
                budget(deadline, &cancel)?;
                let length = (requested - offset).min(16384);
                let mut chunk = p.clone();
                chunk["size_bytes"] = json!(length);
                chunk["address"] = json!(format!("0x{:x}", address + offset));
                let result = state
                    .backends
                    .get_mut(&backend_name)
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?
                    .request(op, chunk, deadline, &cancel)?;
                let bytes = result["bytes_hex"]
                    .as_str()
                    .filter(|s| {
                        s.len() == length as usize * 2 && s.bytes().all(|b| b.is_ascii_hexdigit())
                    })
                    .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
                combined.push_str(bytes);
                offset += length;
            }
            json!({"bytes_hex":combined,"address":text,"size_bytes":requested})
        } else {
            state
                .backends
                .get_mut(&backend_name)
                .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?
                .request(op, p, deadline, &cancel)?
        };
        Self::events(&mut state);
        let resource = self.handle(&mut state, &id, &r, closing)?;
        if let Some(next) = value["next_cursor"].as_str() {
            resource.cursor_sequence += 1;
            if resource.cursors.len() >= 128 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            resource
                .cursors
                .insert(resource.cursor_sequence, next.into());
            value["next_cursor"] =
                json!(self
                    .cursor
                    .encode(resource.cursor_sequence, &scope, &revision)?);
        }
        if matches!(op, "re.command" | "debugger.command") {
            resource.revision += 1;
            resource.page_revision += 1;
            resource.cursors.clear();
        }
        for key in [
            "debugger_state",
            "stop_sequence",
            "stop_reason",
            "analysis_state",
        ] {
            if let Some(v) = value.get(key) {
                resource.handle[key] = v.clone();
            }
        }
        if closing {
            resource.active = false;
            resource.handle["state"] = json!("CLOSED");
        }
        resource.handle["resource_revision"] = json!(resource.revision.to_string());
        value["handle"] = resource.handle.clone();
        if closing {
            self.remove_resource(&resource.root)?;
        }
        if op == "debugger.read_memory" {
            let raw = value["bytes_hex"]
                .as_str()
                .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
            if raw.len() > 2 * 1024 * 1024
                || raw.len() % 2 != 0
                || !raw.bytes().all(|b| b.is_ascii_hexdigit())
            {
                return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
            }
            let bytes: Vec<u8> = raw
                .as_bytes()
                .chunks_exact(2)
                .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
                .collect();
            value.as_object_mut().unwrap().remove("bytes_hex");
            value["sha256"] = json!(digest(&bytes));
            value["size_bytes"] = json!(bytes.len());
            if bytes.len() <= 4096 {
                use base64::Engine;
                value["data"] = json!(base64::engine::general_purpose::STANDARD.encode(&bytes));
                value["encoding"] = json!("base64");
            } else {
                let path = self.spool.join(format!(
                    "{}.debug-memory.bin",
                    r["operation_id"].as_str().unwrap()
                ));
                let mut file = secure_create_file(&path)?;
                file.write_all(&bytes)?;
                file.sync_all()?;
                value["spool_path"] = json!(path);
                value["media_type"] = json!("application/octet-stream");
            }
        }
        Ok(value)
    }
}
impl Provider for Reversing {
    fn capabilities(&self) -> Vec<Value> {
        let state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        ["re","debugger"].into_iter().map(|namespace|{let mut operations=BTreeSet::from([format!("{namespace}.backends")]);for backend in state.backends.values().filter(|b|b.ready){for op in backend.approved.manifest["operations"].as_array().into_iter().flatten(){if op["name"].as_str().is_some_and(|s|s.starts_with(&format!("{namespace}."))){operations.insert(op["name"].as_str().unwrap().into());}}}json!({"name":namespace,"version":"1.0.0","supported":true,"enabled":true,"healthy":true,"operations":operations,"attributes":{"resource_limit":8,"history_limit":64,"idle_ttl_seconds":3600,"plugin_crash_isolation":"rust-subprocess-job","configuration_errors":state.errors}})}).collect()
    }
    fn inventory(&self) -> Vec<Value> {
        self.state
            .lock()
            .map(|s| s.resources.values().map(|r| r.handle.clone()).collect())
            .unwrap_or_default()
    }
    fn execute(
        &self,
        r: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        let provider = self.clone();
        let deadline = Instant::now()
            + Duration::from_millis(r["remaining_timeout_ms"].as_u64().unwrap_or(30000));
        Box::pin(async move {
            let result = tokio::task::spawn_blocking(move || provider.run(r, cancel, deadline))
                .await
                .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))??;
            Ok(json!({"state":"SUCCEEDED","result":result,"error":null}))
        })
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        let provider = self.clone();
        Box::pin(async move {
            tokio::task::spawn_blocking(move || {
                let mut state = provider
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
                for backend in state.backends.values_mut() {
                    backend.shutdown()?;
                }
                for resource in state.resources.values_mut() {
                    resource.active = false;
                    resource.handle["state"] = json!("CLOSED");
                    provider.remove_resource(&resource.root)?;
                }
                Ok(())
            })
            .await
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))?
        })
    }
}
