//! Same Agent binary hosts fixed native adapters. Stdout is plugin frames only.
use super::{
    gdb::{target_metadata, Gdb},
    ghidra::{file_hash, Ghidra},
    plugin,
};
use racp_contract::{digest, new_id, validate_operation, validate_schema, RacpError};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::{BufRead, Read, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
fn argument(args: &[String], flag: &str) -> Result<String, RacpError> {
    let mut found = args.windows(2).filter(|p| p[0] == flag);
    let value = found
        .next()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?[1]
        .clone();
    if found.next().is_some() {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    Ok(value)
}
enum Adapter {
    Gdb {
        executable: PathBuf,
        hash: String,
        sessions: BTreeMap<String, Gdb>,
    },
    Ghidra {
        runtime: Ghidra,
        sessions: BTreeMap<String, PathBuf>,
    },
}
struct Worker {
    manifest: Value,
    manifest_path: PathBuf,
    manifest_hash: String,
    instance: Option<String>,
    sequence: u64,
    reported: BTreeMap<String, (u64, String)>,
    adapter: Adapter,
}
impl Worker {
    fn publish(&mut self, stdout: &mut impl Write) -> Result<(), RacpError> {
        if let Adapter::Gdb { sessions, .. } = &mut self.adapter {
            if let Some(instance) = &self.instance {
                for (id, driver) in sessions {
                    driver.observe_pending(
                        Instant::now() + Duration::from_millis(100),
                        &CancellationToken::new(),
                    )?;
                    if self.reported.get(id).is_none_or(|(sequence, state)| {
                        *sequence != driver.sequence || *state != driver.state
                    }) {
                        self.reported
                            .insert(id.clone(), (driver.sequence, driver.state.clone()));
                        self.sequence = self
                            .sequence
                            .checked_add(1)
                            .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
                        let kind = if driver.state == "STOPPED" {
                            "debugger.stopped"
                        } else if driver.state == "EXITED" {
                            "debugger.exited"
                        } else {
                            "debugger.running"
                        };
                        let mut data = driver.info();
                        data["resource_id"] = json!(id);
                        let frame = json!({"type":"event","protocol_version":1,"instance_id":instance,"sequence":self.sequence.to_string(),"kind":kind,"data":data});
                        write_frame(stdout, &frame)?;
                    }
                }
            }
        }
        Ok(())
    }
    fn execute(&mut self, r: &Value) -> Result<Value, RacpError> {
        let now = chrono::Utc::now().timestamp_millis();
        let requested = r["deadline_unix_ms"]
            .as_i64()
            .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
        if requested <= now {
            return Err(RacpError::new("TIMEOUT"));
        }
        let deadline =
            Instant::now() + Duration::from_millis((requested - now).min(86400000) as u64);
        let cancel = CancellationToken::new();
        let instance = r["instance_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
        if let Some(current) = &self.instance {
            if current != instance {
                return Err(RacpError::new("HANDLE_EXPIRED"));
            }
        } else {
            self.instance = Some(instance.into());
        }
        if digest(racp_core::read_bounded(
            &self.manifest_path,
            256 * 1024,
            false,
        )?) != self.manifest_hash
        {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let op = r["operation"]
            .as_str()
            .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
        if op == "plugin.health" {
            match &mut self.adapter {
                Adapter::Gdb {
                    executable, hash, ..
                } => {
                    if file_hash(executable, deadline, &cancel)? != *hash {
                        return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
                    }
                    super::gdb::probe(
                        executable,
                        Path::new(self.manifest["working_directory"].as_str().unwrap()),
                        self.manifest["backend_version"].as_str().unwrap(),
                        deadline,
                        &cancel,
                    )?;
                }
                Adapter::Ghidra { runtime, .. } => runtime.health(
                    Path::new(self.manifest["working_directory"].as_str().unwrap()),
                    deadline,
                    &cancel,
                )?,
            }
            return Ok(
                json!({"manifest_sha256":self.manifest_hash,"backend_version":self.manifest["backend_version"]}),
            );
        }
        if !self.manifest["operations"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["name"] == op)
        {
            return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
        }
        let p = validate_operation(op, r["payload"].clone())?;
        match &mut self.adapter {
            Adapter::Gdb {
                executable,
                hash,
                sessions,
            } => {
                if file_hash(executable, deadline, &cancel)? != *hash {
                    return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
                }
                if op == "debugger.launch" {
                    if sessions.len() >= 4 {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    let target = Path::new(p["executable"].as_str().unwrap());
                    let mut metadata = target_metadata(target)?;
                    let mut driver = Gdb::start(
                        executable,
                        target
                            .parent()
                            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?,
                        deadline,
                        &cancel,
                    )?;
                    let args: Vec<_> = p["args"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .map(|s| s.as_str().unwrap().into())
                        .collect();
                    driver.launch(target, &args, deadline, &cancel)?;
                    let id = new_id("gdb");
                    metadata["resource_id"] = json!(id);
                    metadata
                        .as_object_mut()
                        .unwrap()
                        .extend(driver.info().as_object().unwrap().clone());
                    sessions.insert(id, driver);
                    return Ok(metadata);
                }
                let id = p["debug_id"].as_str().unwrap_or("");
                let driver = sessions
                    .get_mut(id)
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
                let result = driver.execute(op, &p, deadline, &cancel);
                if op == "debugger.close" && result.is_ok() {
                    sessions.remove(id);
                    self.reported.remove(id);
                }
                result
            }
            Adapter::Ghidra { runtime, sessions } => {
                if op == "re.open" {
                    if sessions.len() >= 8 {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    let target = PathBuf::from(p["path"].as_str().unwrap());
                    let hash = file_hash(&target, deadline, &cancel)?;
                    let info = runtime.call(
                        &target,
                        &json!({"action":"info"}),
                        true,
                        deadline,
                        &cancel,
                    )?;
                    let id = new_id("ghidra");
                    let result = json!({"resource_id":id,"target_sha256":hash,"architecture":info["architecture"],"image_base":info["image_base"],"analysis_state":"READY","analysis_database":target.parent().unwrap().join("database/analysis.gpr")});
                    sessions.insert(id, target);
                    return Ok(result);
                }
                let id = p["analysis_id"].as_str().unwrap_or("");
                let target = sessions
                    .get(id)
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?
                    .clone();
                if op == "re.close" {
                    sessions.remove(id);
                    self.reported.remove(id);
                    return Ok(json!({"closed":true,"analysis_state":"CLOSED"}));
                }
                if matches!(op, "re.query" | "re.command") {
                    return runtime.call(&target, &p, false, deadline, &cancel);
                }
                Err(RacpError::new("OPERATION_NOT_SUPPORTED"))
            }
        }
    }
}
fn write_frame(output: &mut impl Write, value: &Value) -> Result<(), RacpError> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    if bytes.len() > 1024 * 1024 {
        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
    }
    output.write_all(&bytes)?;
    output.flush()?;
    Ok(())
}
pub fn run_native_plugin(mode: &str, args: &[String]) -> Result<(), RacpError> {
    let manifest_path = PathBuf::from(argument(args, "--manifest")?);
    racp_core::validate_local_path(&manifest_path)?;
    let raw = racp_core::read_bounded(&manifest_path, 256 * 1024, false)?;
    let manifest = plugin::decode(&raw, 256 * 1024)?;
    let schema: Value = serde_json::from_str(include_str!(
        "../../../../../docs/protocol/plugin-manifest-v1.schema.json"
    ))
    .expect("manifest schema");
    validate_schema(&schema, &manifest)?;
    let adapter = if mode == "plugin-gdb" {
        let executable = PathBuf::from(argument(args, "--gdb")?);
        racp_core::validate_local_path(&executable)?;
        let hash = argument(args, "--gdb-sha256")?;
        if hash.len() != 64
            || !hash
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        Adapter::Gdb {
            executable,
            hash,
            sessions: BTreeMap::new(),
        }
    } else if mode == "plugin-ghidra" {
        let catalog = PathBuf::from(argument(args, "--runtime-catalog")?);
        let hash = argument(args, "--runtime-catalog-sha256")?;
        Adapter::Ghidra {
            runtime: Ghidra::load(&catalog, &hash)?,
            sessions: BTreeMap::new(),
        }
    } else {
        return Err(RacpError::new("REQUEST_INVALID"));
    };
    let mut worker = Worker {
        manifest,
        manifest_path,
        manifest_hash: digest(&raw),
        instance: None,
        sequence: 0,
        reported: BTreeMap::new(),
        adapter,
    };
    let input = std::io::stdin();
    let mut input = input.lock();
    let output = std::io::stdout();
    let mut output = output.lock();
    let protocol: Value = serde_json::from_str(include_str!(
        "../../../../../docs/protocol/plugin-stdio-v1.schema.json"
    ))
    .expect("stdio schema");
    loop {
        let mut bytes = vec![];
        let mut bounded = (&mut input).take(1024 * 1024 + 1);
        let n = bounded.read_until(b'\n', &mut bytes)?;
        if n == 0 {
            break;
        }
        if bytes.len() > 1024 * 1024 || !bytes.ends_with(b"\n") {
            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
        }
        let request = plugin::decode(&bytes, 1024 * 1024)?;
        validate_schema(&protocol, &request)?;
        if request["type"] == "cancel" {
            continue;
        }
        if request["type"] != "request" {
            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
        }
        worker.publish(&mut output)?;
        let result = worker.execute(&request);
        let response = match result {
            Ok(result) => {
                json!({"type":"result","protocol_version":1,"instance_id":request["instance_id"],"request_id":request["request_id"],"state":"SUCCEEDED","result":result,"error":null})
            }
            Err(e) => {
                json!({"type":"result","protocol_version":1,"instance_id":request["instance_id"],"request_id":request["request_id"],"state":"FAILED","result":null,"error":{"code":e.code.0,"message":"native adapter operation interrupted or rejected","execution_state":if matches!(e.code.0,"EXECUTION_UNKNOWN"|"TIMEOUT"|"GDB_TRANSPORT_FAILED"){"unknown"}else{"not_started"}}})
            }
        };
        write_frame(&mut output, &response)?;
        worker.publish(&mut output)?;
    }
    if let Adapter::Gdb { sessions, .. } = &mut worker.adapter {
        for driver in sessions.values_mut() {
            driver.close()?;
        }
    }
    Ok(())
}
