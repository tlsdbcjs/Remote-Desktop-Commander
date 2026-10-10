//! Locally approved subprocess protocol; fixed public operation registry and no replay.
use crate::{
    identity::ProtectedProcess,
    providers::containment::{self, CommandSpec, OwnedProcess},
};
use racp_contract::{digest, new_id, validate_schema, RacpError};
use racp_core::{read_bounded, validate_local_path, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::{BTreeSet, VecDeque},
    fs::File,
    io::Write,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
fn schema(manifest: bool) -> Value {
    serde_json::from_str(if manifest {
        include_str!("../../../../../docs/protocol/plugin-manifest-v1.schema.json")
    } else {
        include_str!("../../../../../docs/protocol/plugin-stdio-v1.schema.json")
    })
    .expect("plugin schema")
}
fn references(v: &Value) -> bool {
    match v {
        Value::Object(o) => o.iter().all(|(k, v)| {
            if matches!(k.as_str(), "$ref" | "$dynamicRef") {
                v.as_str().is_some_and(|r| r.starts_with("#/"))
            } else {
                references(v)
            }
        }),
        Value::Array(a) => a.iter().all(references),
        _ => true,
    }
}
fn tree(v: &Value, depth: usize, nodes: &mut usize) -> bool {
    *nodes += 1;
    if depth > 16 || *nodes > 10000 {
        return false;
    }
    match v {
        Value::Object(o) => o.values().all(|v| tree(v, depth + 1, nodes)),
        Value::Array(a) => a.iter().all(|v| tree(v, depth + 1, nodes)),
        _ => true,
    }
}
pub fn decode(raw: &[u8], limit: usize) -> Result<Value, RacpError> {
    if raw.is_empty() || raw.len() > limit {
        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
    }
    struct Strict(Value);
    impl<'de> serde::Deserialize<'de> for Strict {
        fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
            struct Visitor;
            impl<'de> serde::de::Visitor<'de> for Visitor {
                type Value = Strict;
                fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                    f.write_str("strict JSON")
                }
                fn visit_bool<E: serde::de::Error>(self, v: bool) -> Result<Strict, E> {
                    Ok(Strict(json!(v)))
                }
                fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<Strict, E> {
                    Ok(Strict(json!(v)))
                }
                fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<Strict, E> {
                    Ok(Strict(json!(v)))
                }
                fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Strict, E> {
                    if !v.is_finite() {
                        return Err(E::custom("nonfinite"));
                    }
                    Ok(Strict(json!(v)))
                }
                fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Strict, E> {
                    Ok(Strict(json!(v)))
                }
                fn visit_string<E: serde::de::Error>(self, v: String) -> Result<Strict, E> {
                    Ok(Strict(json!(v)))
                }
                fn visit_unit<E: serde::de::Error>(self) -> Result<Strict, E> {
                    Ok(Strict(Value::Null))
                }
                fn visit_seq<A: serde::de::SeqAccess<'de>>(
                    self,
                    mut a: A,
                ) -> Result<Strict, A::Error> {
                    let mut v = vec![];
                    while let Some(Strict(item)) = a.next_element()? {
                        if v.len() >= 10000 {
                            return Err(serde::de::Error::custom("nodes"));
                        }
                        v.push(item);
                    }
                    Ok(Strict(json!(v)))
                }
                fn visit_map<A: serde::de::MapAccess<'de>>(
                    self,
                    mut a: A,
                ) -> Result<Strict, A::Error> {
                    let mut v = serde_json::Map::new();
                    while let Some(key) = a.next_key::<String>()? {
                        if v.contains_key(&key) || v.len() >= 10000 {
                            return Err(serde::de::Error::custom("duplicate or nodes"));
                        }
                        let Strict(item) = a.next_value()?;
                        v.insert(key, item);
                    }
                    Ok(Strict(Value::Object(v)))
                }
            }
            d.deserialize_any(Visitor)
        }
    }
    let Strict(value) =
        serde_json::from_slice(raw).map_err(|_| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
    if !value.is_object() || !tree(&value, 0, &mut 0) {
        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
    }
    Ok(value)
}
pub struct Approved {
    pub manifest: Value,
    pub hash: String,
    pub path: PathBuf,
}
impl Approved {
    pub fn load(
        path: &Path,
        hash: &str,
        permissions: &BTreeSet<String>,
    ) -> Result<Self, RacpError> {
        validate_local_path(path)?;
        let raw = read_bounded(path, 256 * 1024, false)?;
        if digest(&raw) != hash {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let manifest = decode(&raw, 256 * 1024)?;
        validate_schema(&schema(true), &manifest)?;
        let required: BTreeSet<_> = manifest["required_permissions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_owned())
            .collect();
        if !required.is_subset(permissions) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let executable = Path::new(manifest["command"][0].as_str().unwrap());
        let cwd = Path::new(manifest["working_directory"].as_str().unwrap());
        validate_local_path(executable)?;
        validate_local_path(cwd)?;
        if !executable.is_file() || !cwd.is_dir() {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let mut operations = BTreeSet::new();
        for operation in manifest["operations"].as_array().unwrap() {
            let name = operation["name"].as_str().unwrap();
            let expected = if name.starts_with("re.") {
                "static-analysis"
            } else if name.starts_with("debugger.") {
                "debugger"
            } else {
                return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
            };
            if !operations.insert(name)
                || racp_contract::registry().get(name).is_none()
                || operation["capability"] != expected
                || !manifest["capabilities"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|v| v == expected)
                || !references(&operation["input_schema"])
                || !references(&operation["output_schema"])
                || serde_json::to_vec(&operation["input_schema"])?.len() > 16384
                || serde_json::to_vec(&operation["output_schema"])?.len() > 16384
                || !required.contains(operation["permission_scope"].as_str().unwrap())
            {
                return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
            }
        }
        Ok(Self {
            manifest,
            hash: hash.into(),
            path: path.into(),
        })
    }
}
pub struct Supervisor {
    pub approved: Approved,
    pub instance: String,
    pub ready: bool,
    process: Option<OwnedProcess>,
    input: Option<File>,
    protection: Option<ProtectedProcess>,
    buffer: Vec<u8>,
    stderr_bytes: usize,
    pub events: VecDeque<Value>,
    sequence: u64,
}
impl Supervisor {
    pub fn new(approved: Approved) -> Self {
        Self {
            approved,
            instance: new_id("provider"),
            ready: false,
            process: None,
            input: None,
            protection: None,
            buffer: vec![],
            stderr_bytes: 0,
            events: VecDeque::new(),
            sequence: 0,
        }
    }
    fn launch(&mut self) -> Result<(), RacpError> {
        if self.process.is_some() {
            return Ok(());
        }
        if digest(read_bounded(&self.approved.path, 256 * 1024, false)?) != self.approved.hash {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let m = &self.approved.manifest;
        let root = Path::new(m["working_directory"].as_str().unwrap());
        let guards = Workspaces::new(root, &[])?;
        let (process, input) = OwnedProcess::spawn_stdio(CommandSpec {
            argv: m["command"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().into())
                .collect(),
            environment: containment::environment(&m["environment"])?,
            cwd: guards.directory("default", root)?,
        })?;
        #[cfg(windows)]
        process.limit_memory(m["max_memory_bytes"].as_u64().unwrap_or(2147483648))?;
        self.protection = Some(ProtectedProcess::register(process.pid())?);
        self.process = Some(process);
        self.input = Some(input);
        Ok(())
    }
    pub fn health(&mut self, cancel: &CancellationToken) -> Result<Value, RacpError> {
        self.launch()?;
        let result = self.request(
            "plugin.health",
            json!({}),
            Instant::now()
                + Duration::from_millis(
                    self.approved.manifest["health_timeout_ms"]
                        .as_u64()
                        .unwrap_or(5000),
                ),
            cancel,
        )?;
        if result["manifest_sha256"] != self.approved.hash
            || result["backend_version"] != self.approved.manifest["backend_version"]
        {
            let _ = self.shutdown();
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        self.ready = true;
        Ok(result)
    }
    pub fn request(
        &mut self,
        operation: &str,
        payload: Value,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        if operation != "plugin.health" {
            let spec = self.approved.manifest["operations"]
                .as_array()
                .unwrap()
                .iter()
                .find(|s| s["name"] == operation)
                .ok_or_else(|| RacpError::new("OPERATION_NOT_SUPPORTED"))?;
            validate_schema(&spec["input_schema"], &payload)?;
        }
        self.launch()?;
        let id = new_id("req");
        let mut raw = serde_json::to_vec(
            &json!({"type":"request","protocol_version":1,"instance_id":self.instance,"request_id":id,"operation":operation,"deadline_unix_ms":chrono::Utc::now().timestamp_millis()+deadline.saturating_duration_since(Instant::now()).as_millis() as i64,"payload":payload}),
        )?;
        raw.push(b'\n');
        if raw.len() > 1024 * 1024 {
            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
        }
        let mut stdin = self
            .input
            .as_ref()
            .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?
            .try_clone()?;
        let writer = std::thread::spawn(move || stdin.write_all(&raw));
        let mut result = (|| {
            let mut chunk = [0u8; 8192];
            loop {
                if cancel.is_cancelled() {
                    return Err(RacpError::new("CANCELLED"));
                }
                if Instant::now() >= deadline {
                    return Err(RacpError::new("TIMEOUT"));
                }
                let process = self
                    .process
                    .as_mut()
                    .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
                match process.stderr.read_available(&mut chunk) {
                    Ok(Some(n)) => self.stderr_bytes += n,
                    Ok(None) => (),
                    Err(_) => return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR")),
                };
                if self.stderr_bytes > 8 * 1024 * 1024 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
                match process.stdout.read_available(&mut chunk) {
                    Ok(Some(n)) => self.buffer.extend_from_slice(&chunk[..n]),
                    Ok(None) => (),
                    Err(_) => return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR")),
                };
                if self.buffer.len() > 1024 * 1024 {
                    return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                }
                while let Some(end) = self.buffer.iter().position(|b| *b == b'\n') {
                    let frame: Vec<_> = self.buffer.drain(..=end).collect();
                    let message = decode(&frame, 1024 * 1024)?;
                    validate_schema(&schema(false), &message)?;
                    if message["instance_id"] != self.instance {
                        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                    }
                    if message["type"] == "event" {
                        let sequence = message["sequence"]
                            .as_str()
                            .and_then(|s| s.parse::<u64>().ok())
                            .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
                        if sequence <= self.sequence || self.events.len() >= 64 {
                            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                        }
                        self.sequence = sequence;
                        self.events.push_back(message);
                        continue;
                    }
                    if message["type"] != "result" || message["request_id"] != id {
                        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                    }
                    if message["state"] != "SUCCEEDED" {
                        return Err(RacpError::new("PLUGIN_OPERATION_FAILED"));
                    }
                    let value = message["result"].clone();
                    if operation != "plugin.health" {
                        let spec = self.approved.manifest["operations"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .find(|s| s["name"] == operation)
                            .unwrap();
                        validate_schema(&spec["output_schema"], &value)?;
                    }
                    return Ok(value);
                }
                if process.poll()?.is_some() {
                    return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                }
                std::thread::sleep(Duration::from_millis(3));
            }
        })();
        while !writer.is_finished() {
            if cancel.is_cancelled() || Instant::now() >= deadline {
                result = Err(RacpError::new("EXECUTION_UNKNOWN"));
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        if result.is_err() {
            self.shutdown()?;
        }
        writer
            .join()
            .map_err(|_| RacpError::new("PLUGIN_PROTOCOL_ERROR"))??;
        result
    }
    pub fn shutdown(&mut self) -> Result<(), RacpError> {
        self.ready = false;
        self.input.take();
        if let Some(process) = self.process.as_mut() {
            process.kill_tree()?;
            let deadline = Instant::now() + Duration::from_secs(5);
            while !process.tree_empty()? {
                if Instant::now() >= deadline {
                    return Err(RacpError::new("CLEANUP_FAILED"));
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        self.process.take();
        self.protection.take();
        self.buffer.clear();
        Ok(())
    }
}
impl Drop for Supervisor {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}
