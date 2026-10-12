//! Optional native analysis host. Excluded from the base Client workspace and payload.
mod engine;
use engine::ghidra::{file_hash, Catalog, Ghidra};
use racp_contract::{
    digest, new_id,
    runtime_manifest::{relative_runtime_path, RuntimeFileRole, RuntimeId},
    validate_operation, validate_schema, RacpError,
};
use racp_core::{
    runtime_admission::{pin, AdmissionBudget, PinnedRuntime},
    Workspaces,
};
use racp_runtime::tool_support::decode;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::{BufRead, Read, Write},
    path::{Path, PathBuf},
    sync::{atomic::AtomicBool, Arc},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HostRuntime {
    schema_version: u32,
    installation: String,
    java: String,
}
struct Session {
    target: PathBuf,
    sha256: String,
}
fn current_bundle() -> Result<PinnedRuntime, RacpError> {
    let exe = racp_core::validate_local_path(&std::env::current_exe()?)?;
    let mut parent = exe.parent();
    for _ in 0..32 {
        let Some(directory) = parent else {
            break;
        };
        if directory.join("admitted-manifest.json").try_exists()? {
            let id = directory
                .file_name()
                .and_then(|value| value.to_str())
                .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
            let state = directory
                .parent()
                .and_then(Path::parent)
                .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
            let budget = AdmissionBudget {
                deadline: Instant::now() + Duration::from_secs(120),
                cancelled: Arc::new(AtomicBool::new(false)),
            };
            let runtime = pin(state, id, &budget)?;
            if runtime.manifest.runtime.id != RuntimeId::Ghidra || runtime.entrypoint != exe {
                return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
            }
            racp_core::runtime_admission::validate_selection(&runtime.manifest)?;
            return Ok(runtime);
        }
        parent = directory.parent();
    }
    Err(RacpError::new("RUNTIME_MANIFEST_UNTRUSTED"))
}
fn catalog(runtime: &PinnedRuntime, work: &Path) -> Result<Ghidra, RacpError> {
    let bundle = runtime
        .entrypoint
        .ancestors()
        .find(|path| path.join("admitted-manifest.json").is_file())
        .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
    let definition = runtime
        .manifest
        .files
        .iter()
        .find(|file| file.path == "host-runtime.json" && file.role == RuntimeFileRole::Data)
        .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
    let bytes = racp_core::read_bounded(&bundle.join(&definition.path), 65536, false)?;
    let definition = decode(&bytes, 65536)?;
    let schema: Value = serde_json::from_str(include_str!(
        "../../../docs/protocol/analysis-host-runtime-v1.schema.json"
    ))?;
    validate_schema(&schema, &definition)?;
    let spec: HostRuntime = serde_json::from_value(definition)?;
    if spec.schema_version != 1
        || !relative_runtime_path(&spec.installation)
        || !relative_runtime_path(&spec.java)
    {
        return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
    }
    let java = runtime
        .manifest
        .files
        .iter()
        .find(|file| file.path == spec.java && file.role == RuntimeFileRole::NativeBinary)
        .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
    let mut files = BTreeMap::new();
    for file in &runtime.manifest.files {
        if let Ok(relative) = Path::new(&file.path).strip_prefix(&spec.installation) {
            if !relative.as_os_str().is_empty() {
                files.insert(
                    relative.to_string_lossy().replace('\\', "/"),
                    file.sha256.to_ascii_lowercase(),
                );
            }
        }
    }
    if files.is_empty() {
        return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
    }
    let catalog = Catalog {
        version: runtime.manifest.runtime.version.clone(),
        installation: bundle.join(spec.installation),
        java: bundle.join(spec.java),
        java_sha256: java.sha256.to_ascii_lowercase(),
        files,
    };
    let bytes = serde_json::to_vec(&catalog)?;
    let guards = Workspaces::new(work, &[])?;
    let directory = guards.directory("default", work)?;
    let name = format!("host-catalog-{}.json", digest(&bytes));
    if directory.info(&name)?.is_some() {
        if racp_core::read_bounded(&work.join(&name), 4 * 1024 * 1024, true)? != bytes {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
    } else {
        let mut file = directory.create(&name)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
    }
    Ghidra::load(&work.join(name), &digest(bytes))
}
fn execute(
    engine: &mut Ghidra,
    manifest: &Value,
    hash: &str,
    sessions: &mut BTreeMap<String, Session>,
    request: &Value,
    effect_started: &mut bool,
) -> Result<Value, RacpError> {
    let now = chrono::Utc::now().timestamp_millis();
    let deadline_ms = request["deadline_unix_ms"]
        .as_i64()
        .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
    let remaining = deadline_ms
        .checked_sub(now)
        .filter(|value| *value > 0 && *value <= 86400000)
        .ok_or_else(|| RacpError::new("TIMEOUT"))?;
    let deadline = Instant::now() + Duration::from_millis(remaining as u64);
    let cancel = CancellationToken::new();
    let operation = request["operation"].as_str().unwrap_or("");
    if operation == "plugin.health" {
        if engine.catalog.version != manifest["backend_version"].as_str().unwrap_or("") {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        engine.health(
            Path::new(manifest["working_directory"].as_str().unwrap()),
            deadline,
            &cancel,
        )?;
        return Ok(json!({"manifest_sha256":hash,"backend_version":engine.catalog.version}));
    }
    if !manifest["operations"]
        .as_array()
        .is_some_and(|operations| operations.iter().any(|spec| spec["name"] == operation))
    {
        return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
    }
    let payload = validate_operation(operation, request["payload"].clone())?;
    if operation == "re.open" {
        if sessions.len() >= 8 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let target = PathBuf::from(payload["path"].as_str().unwrap());
        let work = Path::new(manifest["working_directory"].as_str().unwrap());
        let root = work
            .parent()
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        let checked = racp_core::validate_local_path(&target)?;
        let parent = checked
            .parent()
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        let resource = parent
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("");
        if parent.parent() != Some(root)
            || !resource.starts_with("analysis_")
            || resource.len() != 41
            || !resource[9..].bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let guards = Workspaces::new(root, &[])?;
        let directory = guards.directory("default", parent)?;
        let name = checked
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        if directory.require_file(name)?.size > 1024 * 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let sha256 = file_hash(&target, deadline, &cancel)?;
        *effect_started = true;
        let info = engine.call(&target, &json!({"action":"info"}), true, deadline, &cancel)?;
        if file_hash(&target, deadline, &cancel)? != sha256 {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let id = new_id("analysis");
        let result = json!({"resource_id":id,"target_sha256":sha256,"architecture":info["architecture"],"image_base":info["image_base"],
            "analysis_state":"READY","analysis_database":target.parent().unwrap().join("database/analysis.gpr")});
        sessions.insert(id, Session { target, sha256 });
        return Ok(result);
    }
    let id = payload["analysis_id"].as_str().unwrap_or("");
    let session = sessions
        .get(id)
        .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
    if operation == "re.close" {
        sessions.remove(id);
        return Ok(json!({"closed":true,"analysis_state":"CLOSED"}));
    }
    if file_hash(&session.target, deadline, &cancel)? != session.sha256 {
        return Err(RacpError::new("PRECONDITION_FAILED"));
    }
    if operation == "re.keepalive" {
        return Ok(json!({"analysis_state":"READY","target_sha256":session.sha256}));
    }
    if matches!(operation, "re.query" | "re.command") {
        *effect_started = operation == "re.command";
        return engine.call(&session.target, &payload, false, deadline, &cancel);
    }
    Err(RacpError::new("OPERATION_NOT_SUPPORTED"))
}
fn run(manifest_path: &Path) -> Result<(), RacpError> {
    let _runtime = current_bundle()?;
    let bytes = racp_core::read_bounded(manifest_path, 256 * 1024, false)?;
    let hash = digest(&bytes);
    let manifest = decode(&bytes, 256 * 1024)?;
    let schema: Value = serde_json::from_str(include_str!(
        "../../../docs/protocol/plugin-manifest-v1.schema.json"
    ))?;
    validate_schema(&schema, &manifest)?;
    if manifest["name"] != "ghidra" || manifest["backend_name"] != "native-analysis-host" {
        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
    }
    let work = Path::new(manifest["working_directory"].as_str().unwrap());
    racp_core::verify_private_native_directory(work)?;
    let mut engine = catalog(&_runtime, work)?;
    let mut sessions = BTreeMap::new();
    let protocol: Value = serde_json::from_str(include_str!(
        "../../../docs/protocol/plugin-stdio-v1.schema.json"
    ))?;
    let mut input = std::io::stdin().lock();
    let mut output = std::io::stdout().lock();
    let mut instance = None;
    loop {
        let mut bytes = Vec::new();
        let n = (&mut input)
            .take(1024 * 1024 + 1)
            .read_until(b'\n', &mut bytes)?;
        if n == 0 {
            break;
        }
        if bytes.len() > 1024 * 1024 || !bytes.ends_with(b"\n") {
            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
        }
        let request = decode(&bytes, 1024 * 1024)?;
        validate_schema(&protocol, &request)?;
        if request["type"] == "cancel" {
            continue;
        }
        if request["type"] != "request" {
            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
        }
        let current = request["instance_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
        if instance.as_ref().is_some_and(|value| value != current) {
            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
        }
        instance = Some(current.to_owned());
        if digest(racp_core::read_bounded(manifest_path, 256 * 1024, false)?) != hash {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let mut effect_started = false;
        let result = execute(
            &mut engine,
            &manifest,
            &hash,
            &mut sessions,
            &request,
            &mut effect_started,
        );
        let response = match result {
            Ok(result) => {
                json!({"type":"result","protocol_version":1,"instance_id":current,"request_id":request["request_id"],"state":"SUCCEEDED","result":result,"error":null})
            }
            Err(error) => {
                json!({"type":"result","protocol_version":1,"instance_id":current,"request_id":request["request_id"],"state":"FAILED","result":null,
                "error":{"code":error.code.0,"message":"analysis host request interrupted or rejected","execution_state":if effect_started||matches!(error.code.0,"EXECUTION_UNKNOWN"){"unknown"}else{"not_started"}}})
            }
        };
        validate_schema(&protocol, &response)?;
        let mut bytes = serde_json::to_vec(&response)?;
        bytes.push(b'\n');
        if bytes.len() > 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        output.write_all(&bytes)?;
        output.flush()?;
    }
    Ok(())
}
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args == ["--version"] {
        println!("racp-analysis-host {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if args.len() != 2 || args[0] != "--manifest" {
        eprintln!("usage: racp-analysis-host --manifest <local-adapter-manifest>");
        std::process::exit(4);
    }
    if let Err(error) = run(Path::new(&args[1])) {
        eprintln!("{}", error.code.0);
        std::process::exit(4);
    }
}
