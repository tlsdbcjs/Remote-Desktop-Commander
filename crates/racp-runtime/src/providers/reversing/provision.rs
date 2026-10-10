//! Explicit local provisioning; no network download or PATH discovery.
use super::{
    ghidra::{file_hash, Catalog},
    plugin::{self, Approved},
};
use racp_contract::{digest, registry, validate_schema, RacpError};
use racp_core::{atomic_write, private_dir, read_bounded, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
fn argument(args: &[String], name: &str) -> Result<String, RacpError> {
    let mut matches = args.windows(2).filter(|v| v[0] == name);
    let value = matches
        .next()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?[1]
        .clone();
    if matches.next().is_some() {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    Ok(value)
}
pub fn provision(mode: &str, args: &[String], state: &Path) -> Result<Value, RacpError> {
    let directory = racp_core::validate_local_path(&PathBuf::from(argument(args, "--directory")?))?;
    if directory.try_exists()? {
        return Err(RacpError::new("CONFLICT"));
    }
    private_dir(&directory)?;
    let deadline = Instant::now() + Duration::from_secs(300);
    let cancel = CancellationToken::new();
    let name = if mode == "provision-gdb" {
        "gdb"
    } else {
        "ghidra"
    };
    let manifest_file = directory.join(format!("{name}-manifest.json"));
    let mut command = vec![
        std::env::current_exe()?.to_string_lossy().into_owned(),
        format!("plugin-{name}"),
        "--manifest".into(),
        manifest_file.to_string_lossy().into_owned(),
    ];
    let version;
    let names;
    let category;
    let scopes;
    if name == "gdb" {
        let executable = racp_core::validate_local_path(&PathBuf::from(argument(args, "--gdb")?))?;
        version = argument(args, "--backend-version")?;
        if version.is_empty() || version.len() > 128 || version.chars().any(char::is_control) {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        let hash = file_hash(&executable, deadline, &cancel)?;
        command.extend([
            "--gdb".into(),
            executable.to_string_lossy().into_owned(),
            "--gdb-sha256".into(),
            hash.clone(),
        ]);
        names = vec![
            "debugger.launch",
            "debugger.command",
            "debugger.close",
            "debugger.info",
            "debugger.registers",
            "debugger.read_memory",
            "debugger.backtrace",
            "debugger.wait",
            "debugger.keepalive",
        ];
        category = "debugger";
        scopes = ["debugger.read", "debugger.mutate"];
        atomic_write(
            &directory.join("gdb-backend-provenance.json"),
            &serde_json::to_vec(
                &json!({"backend":"GNU GDB","version":version,"executable":executable,"sha256":hash,"license":"GPL-3.0-or-later","bundled":false,"source":"https://sourceware.org/gdb/","adapter_protocol":1}),
            )?,
            false,
        )?;
    } else {
        let installation =
            racp_core::validate_local_path(&PathBuf::from(argument(args, "--ghidra")?))?;
        let java = racp_core::validate_local_path(&PathBuf::from(argument(args, "--java")?))?;
        let properties = String::from_utf8(read_bounded(
            &installation.join("Ghidra/application.properties"),
            65536,
            false,
        )?)
        .map_err(|_| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
        version = properties
            .lines()
            .find_map(|l| {
                l.trim_end_matches('\r')
                    .strip_prefix("application.version=")
            })
            .ok_or_else(|| RacpError::new("PLUGIN_VERSION_MISMATCH"))?
            .to_owned();
        if version.split('.').count() != 3
            || !version
                .split('.')
                .all(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
        {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let guards = Workspaces::new(&installation, &[])?;
        let mut pending = vec![(installation.clone(), 0u8)];
        let mut files = BTreeMap::new();
        while let Some((path, depth)) = pending.pop() {
            if depth > 64 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let dir = guards.directory("default", &path)?;
            for name in dir.names()? {
                let info = dir
                    .info(&name)?
                    .ok_or_else(|| RacpError::new("PRECONDITION_FAILED"))?;
                if info.link {
                    return Err(RacpError::new("PATH_ACCESS_DENIED"));
                }
                let file = path.join(&name);
                if info.directory {
                    if path != installation || name != "docs" {
                        pending.push((file, depth + 1));
                    }
                } else {
                    if files.len() >= 20000 {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    let relative = file
                        .strip_prefix(&installation)
                        .map_err(|_| RacpError::new("PATH_ACCESS_DENIED"))?
                        .to_string_lossy()
                        .replace('\\', "/");
                    files.insert(relative, file_hash(&file, deadline, &cancel)?);
                }
            }
        }
        let catalog = Catalog {
            version: version.clone(),
            installation,
            java: java.clone(),
            java_sha256: file_hash(&java, deadline, &cancel)?,
            files,
        };
        let catalog_file = directory.join("ghidra-runtime.json");
        let bytes = serde_json::to_vec(&catalog)?;
        atomic_write(&catalog_file, &bytes, false)?;
        command.extend([
            "--runtime-catalog".into(),
            catalog_file.to_string_lossy().into_owned(),
            "--runtime-catalog-sha256".into(),
            digest(&bytes),
        ]);
        names = vec![
            "re.open",
            "re.query",
            "re.command",
            "re.close",
            "re.keepalive",
        ];
        category = "static-analysis";
        scopes = ["re.read", "re.mutate"];
        atomic_write(
            &directory.join("ghidra-backend-provenance.json"),
            &serde_json::to_vec(
                &json!({"backend":"Ghidra","version":version,"bundled":false,"license":"Apache-2.0","runtime_catalog_sha256":digest(&bytes),"java_executable_sha256":catalog.java_sha256,"source":"https://github.com/NationalSecurityAgency/ghidra/","adapter_protocol":1}),
            )?,
            false,
        )?;
    }
    let operations:Vec<_>=names.into_iter().map(|name|{let read=matches!(name,"re.query"|"re.keepalive"|"debugger.info"|"debugger.registers"|"debugger.read_memory"|"debugger.backtrace"|"debugger.wait"|"debugger.keepalive");json!({"name":name,"capability":category,"input_schema":registry()[name]["input_schema"],"output_schema":{"type":"object"},"permission_scope":scopes[if read{0}else{1}],"side_effect":!read,"execution_modes":["sync","job"],"max_timeout_ms":86400000})}).collect();
    let manifest = json!({"name":name,"version":"1.0.0","protocol_version":1,"transport":"stdio","backend_name":if name=="gdb"{"gdb-mi"}else{"ghidra-headless"},"backend_version":version,"capabilities":[category],"command":command,"working_directory":directory,"required_permissions":scopes,"health_timeout_ms":30000,"max_memory_bytes":2147483648u64,"operations":operations});
    let schema: Value = serde_json::from_str(include_str!(
        "../../../../../docs/protocol/plugin-manifest-v1.schema.json"
    ))?;
    validate_schema(&schema, &manifest)?;
    let bytes = serde_json::to_vec(&manifest)?;
    atomic_write(&manifest_file, &bytes, false)?;
    let hash = digest(&bytes);
    Approved::load(
        &manifest_file,
        &hash,
        &scopes
            .into_iter()
            .map(str::to_owned)
            .collect::<BTreeSet<_>>(),
    )?;
    private_dir(state)?;
    let _lock = racp_core::InstanceLock::acquire(&state.join("plugins.lock"))?;
    let path = state.join("plugins.json");
    let mut config = if path.try_exists()? {
        plugin::decode(&read_bounded(&path, 65536, true)?, 65536)?
    } else {
        json!({"version":1,"plugins":[]})
    };
    let schema: Value = serde_json::from_str(include_str!(
        "../../../../../docs/protocol/plugin-installations-v1.schema.json"
    ))?;
    validate_schema(&schema, &config)?;
    let plugins = config["plugins"]
        .as_array_mut()
        .ok_or_else(|| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
    if plugins.len() >= 4 {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    for row in plugins.iter() {
        let prior = plugin::decode(
            &read_bounded(
                Path::new(row["manifest"].as_str().unwrap()),
                256 * 1024,
                false,
            )?,
            256 * 1024,
        )?;
        if prior["name"] == name {
            return Err(RacpError::new("CONFLICT"));
        }
    }
    plugins.push(json!({"manifest":manifest_file,"sha256":hash,"permissions":scopes}));
    validate_schema(&schema, &config)?;
    atomic_write(&path, &serde_json::to_vec(&config)?, true)?;
    Ok(
        json!({"configuration":path,"manifest":manifest_file,"sha256":hash,"backend":name,"restart_required":true}),
    )
}
