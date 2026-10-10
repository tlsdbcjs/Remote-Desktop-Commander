//! Client-local permission ceiling; Gateway profiles can only narrow it.
use racp_contract::{validate_schema, RacpError};
use serde_json::{json, Value};
use std::{collections::BTreeSet, sync::LazyLock};
static CATALOG: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../packages/protocol/src/racp_protocol/permission_catalog.json"
    ))
    .expect("permission catalog")
});
static BINDINGS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("permission-bindings.json")).expect("permission bindings")
});
pub fn legacy_permissions(desktop: bool) -> Value {
    let legacy: Vec<String> = serde_json::from_str(include_str!("legacy-permissions.json"))
        .expect("legacy frozen permissions");
    let grants: serde_json::Map<String, Value> = CATALOG
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| legacy.iter().any(|id| item["id"] == *id))
        .map(|item| {
            (
                item["id"].as_str().unwrap().into(),
                json!(if !desktop
                    && matches!(
                        item["category"].as_str(),
                        Some("desktop_read" | "desktop_input")
                    ) {
                    "deny"
                } else {
                    "allow"
                }),
            )
        })
        .collect();
    json!({"version":1,"grants":grants,"disabled_categories":[],"constraints":{"workspace_ids":[],"max_timeout_ms":86400000,"max_output_bytes":1073741824,"executable_allowlist":[],"executable_denylist":[],"strict_os_isolation":false}})
}
pub fn validate_permissions(value: &Value) -> Result<(), RacpError> {
    let schema: Value = serde_json::from_str(include_str!(
        "../../../docs/protocol/agent-settings-v1.schema.json"
    ))
    .expect("schema");
    validate_schema(
        &json!({"$defs":schema["$defs"],"$ref":"#/$defs/LocalPermissions"}),
        value,
    )?;
    let grants = value["grants"]
        .as_object()
        .ok_or_else(|| RacpError::new("PERMISSIONS_INVALID"))?;
    for (id, grant) in grants {
        let item = CATALOG
            .as_array()
            .unwrap()
            .iter()
            .find(|i| i["id"] == *id)
            .ok_or_else(|| RacpError::new("PERMISSIONS_INVALID"))?;
        if grant != "deny" && item["implementation"] != "rpc" {
            return Err(RacpError::new("PERMISSIONS_INVALID"));
        }
    }
    for category in value["disabled_categories"]
        .as_array()
        .into_iter()
        .flatten()
    {
        if !CATALOG
            .as_array()
            .unwrap()
            .iter()
            .any(|i| i["category"] == *category)
        {
            return Err(RacpError::new("PERMISSIONS_INVALID"));
        }
    }
    for key in ["executable_allowlist", "executable_denylist"] {
        for path in value["constraints"][key].as_array().into_iter().flatten() {
            let p = path.as_str().unwrap_or("");
            if p.is_empty()
                || p.len() > 4096
                || p.contains('\0')
                || !std::path::Path::new(p).is_absolute()
            {
                return Err(RacpError::new("PERMISSIONS_INVALID"));
            }
        }
    }
    Ok(())
}
pub fn desktop_enabled(value: &Value) -> bool {
    CATALOG.as_array().unwrap().iter().any(|i| {
        i["id"]
            .as_str()
            .is_some_and(|id| id.starts_with("desktop.") || id.starts_with("clipboard."))
            && i["implementation"] == "rpc"
            && leaf(value, i["id"].as_str().unwrap()) == "allow"
    })
}
fn leaf<'a>(permissions: &'a Value, id: &str) -> &'a str {
    let item = CATALOG.as_array().unwrap().iter().find(|i| i["id"] == id);
    if item.is_none_or(|i| {
        permissions["disabled_categories"]
            .as_array()
            .is_some_and(|a| a.contains(&i["category"]))
    }) {
        return "deny";
    }
    permissions["grants"][id].as_str().unwrap_or("deny")
}
pub fn permission_allows_field(value: &Value, id: &str) -> bool {
    leaf(value, id) == "allow"
}
pub fn authorize(permissions: &Value, request: &Value, owned: bool) -> Result<(), RacpError> {
    let op = request["operation"].as_str().unwrap_or("");
    let p = &request["payload"];
    let c = &permissions["constraints"];
    let workspace = request["context"]["workspace_id"]
        .as_str()
        .unwrap_or("default");
    let workspaces = c["workspace_ids"].as_array();
    for id in [
        workspace,
        p["destination_workspace_id"].as_str().unwrap_or(workspace),
    ] {
        if workspaces.is_some_and(|a| !a.is_empty() && !a.iter().any(|v| v == id)) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
    }
    if request["timeout_ms"].as_u64().unwrap_or(u64::MAX)
        > c["max_timeout_ms"].as_u64().unwrap_or(86400000)
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    for field in ["max_bytes", "max_output_bytes", "size_bytes"] {
        if p[field]
            .as_u64()
            .is_some_and(|n| n > c["max_output_bytes"].as_u64().unwrap_or(1073741824))
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
    }
    let allow = c["executable_allowlist"].as_array();
    let deny = c["executable_denylist"].as_array();
    if allow.is_some_and(|a| !a.is_empty()) || deny.is_some_and(|a| !a.is_empty()) {
        if op.starts_with("native.") || op.starts_with("proxy.") {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if matches!(
            op,
            "shell.exec" | "process.spawn" | "terminal.open" | "debugger.launch"
        ) {
            let path = if op == "debugger.launch" {
                &p["executable"]
            } else {
                &p["argv"][0]
            };
            let text = path.as_str().unwrap_or("");
            let normalize = |s: &str| {
                let mut normalized = std::path::PathBuf::new();
                for part in std::path::Path::new(s).components() {
                    match part {
                        std::path::Component::ParentDir => {
                            normalized.pop();
                        }
                        std::path::Component::CurDir => (),
                        _ => normalized.push(part.as_os_str()),
                    }
                }
                normalized
                    .to_string_lossy()
                    .replace('/', "\\")
                    .to_lowercase()
            };
            if p["mode"] == "shell"
                || !std::path::Path::new(text).is_absolute()
                || deny.is_some_and(|a| {
                    a.iter()
                        .any(|v| v.as_str().is_some_and(|s| normalize(s) == normalize(text)))
                })
                || allow.is_some_and(|a| {
                    !a.is_empty()
                        && !a
                            .iter()
                            .any(|v| v.as_str().is_some_and(|s| normalize(s) == normalize(text)))
                })
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
        }
    }
    if matches!(op, "native.prepare" | "proxy.prepare")
        && (p["lease_seconds"].as_u64().unwrap_or(120) * 1000
            > c["max_timeout_ms"].as_u64().unwrap_or(86400000)
            || p["max_bytes"].as_u64().unwrap_or(8388608)
                > c["max_output_bytes"].as_u64().unwrap_or(1073741824))
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut required: BTreeSet<&str> = BINDINGS[op]
        .as_array()
        .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    match op {
        "shell.exec" => {
            required.clear();
            required.insert(if p["mode"] == "shell" {
                "exec.shell"
            } else {
                "exec.argv"
            });
        }
        "filesystem.read" if p["binary"] == true => {
            required.clear();
            required.extend(["files.read.binary", "artifacts.export"]);
        }
        "filesystem.write" => {
            required.clear();
            required.insert(if p["mode"].as_str().unwrap_or("create") == "create" {
                "files.create"
            } else {
                "files.edit"
            });
            if !p["artifact_id"].is_null() {
                required.insert("artifacts.import");
            }
        }
        "filesystem.copy" | "filesystem.move" if p["overwrite"] == true => {
            required.remove("files.create");
            required.insert("files.edit");
        }
        "process.terminate" if owned => {
            required.clear();
            required.insert("process.stop.owned");
        }
        "desktop.screenshot" if !p["window_id"].is_null() => {
            required.clear();
            required.extend(["desktop.capture.window", "artifacts.export"]);
        }
        "process.memory_read" if p["size_bytes"].as_u64().unwrap_or(4096) > 4096 => {
            required.insert("artifacts.export");
        }
        "debugger.command" => {
            if matches!(p["action"].as_str(), Some("step_into" | "step_over")) {
                required.clear();
                required.insert("debugger.step");
            } else if matches!(p["action"].as_str(), Some("continue" | "interrupt")) {
                required.clear();
                required.insert("debugger.resume");
            }
        }
        _ => (),
    }
    if matches!(op, "shell.exec" | "process.spawn" | "terminal.open")
        && p["env"].as_object().is_some_and(|v| !v.is_empty())
    {
        required.insert("exec.environment.override");
    }
    let mut approval = false;
    for id in required {
        match leaf(permissions, id) {
            "allow" => (),
            "require_approval" => approval = true,
            _ => return Err(RacpError::new("PERMISSION_DENIED")),
        }
    }
    if approval {
        return Err(RacpError::new("APPROVAL_REQUIRED"));
    }
    Ok(())
}
