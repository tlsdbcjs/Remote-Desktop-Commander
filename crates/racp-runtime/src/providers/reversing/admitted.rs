//! Fixed adapters for admitted tools; never loads a legacy plugins.json or executes Java bridges.
use super::plugin::Approved;
use racp_contract::{digest, registry, RacpError};
use racp_core::{runtime_admission::PinnedRuntime, Workspaces};
use serde_json::{json, Value};
use std::{collections::BTreeSet, path::Path, sync::Arc};
pub(super) fn adapter(
    runtime: Arc<PinnedRuntime>,
    root: &Path,
    gdb: bool,
) -> Result<Approved, RacpError> {
    racp_core::runtime_admission::validate_selection(&runtime.manifest)?;
    let name = if gdb { "gdb" } else { "ghidra" };
    let cwd = root.join(format!(
        "native_adapter_{}",
        racp_contract::canonical_digest(&json!({"runtime":runtime.manifest,"adapter":name}))
    ));
    racp_core::create_private_native_directory(&cwd)?;
    let manifest_path = cwd.join("adapter-manifest.json");
    let command = if gdb {
        vec![
            std::env::current_exe()?.to_string_lossy().into_owned(),
            "plugin-gdb".into(),
            "--manifest".into(),
            manifest_path.to_string_lossy().into_owned(),
            "--gdb".into(),
            runtime.entrypoint.to_string_lossy().into_owned(),
            "--gdb-sha256".into(),
            runtime.manifest.runtime.sha256.to_ascii_lowercase(),
        ]
    } else {
        vec![
            runtime.entrypoint.to_string_lossy().into_owned(),
            "--manifest".into(),
            manifest_path.to_string_lossy().into_owned(),
        ]
    };
    let category = if gdb { "debugger" } else { "static-analysis" };
    let scopes = if gdb {
        ["debugger.read", "debugger.mutate"]
    } else {
        ["re.read", "re.mutate"]
    };
    let names: &[&str] = if gdb {
        &[
            "debugger.launch",
            "debugger.command",
            "debugger.close",
            "debugger.info",
            "debugger.registers",
            "debugger.read_memory",
            "debugger.backtrace",
            "debugger.wait",
            "debugger.keepalive",
        ]
    } else {
        &[
            "re.open",
            "re.query",
            "re.command",
            "re.close",
            "re.keepalive",
        ]
    };
    let operations:Vec<Value>=names.iter().map(|name| {
        let read=matches!(*name,"re.query"|"re.keepalive"|"debugger.info"|"debugger.registers"|"debugger.read_memory"|"debugger.backtrace"|"debugger.wait"|"debugger.keepalive");
        json!({"name":name,"capability":category,"input_schema":registry()[*name]["input_schema"],"output_schema":{"type":"object"},"permission_scope":scopes[if read{0}else{1}],"side_effect":!read,"execution_modes":["sync","job"],"max_timeout_ms":86400000})
    }).collect();
    let manifest = json!({"name":name,"version":"1.0.0","protocol_version":1,"transport":"stdio","backend_name":if gdb {"gdb-mi"} else {"native-analysis-host"},
        "backend_version":runtime.manifest.runtime.version,"capabilities":[category],"command":command,"working_directory":cwd,
        "required_permissions":scopes,"health_timeout_ms":10000,"max_memory_bytes":2147483648u64,"operations":operations});
    let bytes = serde_json::to_vec(&manifest)?;
    racp_core::atomic_write(&manifest_path, &bytes, true)?;
    let permissions: BTreeSet<String> = scopes.into_iter().map(str::to_owned).collect();
    let mut approved = Approved::load(&manifest_path, &digest(&bytes), &permissions)?;
    let guard = Workspaces::new(&cwd, &[])?;
    let directory = guard.directory("default", &cwd)?;
    let file = directory.open_pinned_read("adapter-manifest.json")?;
    approved._pins = Some(Arc::new(super::plugin::AdapterPins {
        _runtime: runtime,
        _guard: guard,
        _directory: directory,
        _manifest: file,
    }));
    Ok(approved)
}
