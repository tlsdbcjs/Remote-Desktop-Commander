//! Static requirements audit. This command never executes Providers or acceptance tests.
use racp_contract::{canonical_digest, digest, registry};
use serde_json::Value;
use std::{collections::BTreeSet, fs, path::Path};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn read(root: &Path, path: &str) -> Result<Value> {
    Ok(serde_json::from_slice(&fs::read(root.join(path))?)?)
}
fn strings(value: &Value) -> Result<BTreeSet<String>> {
    let items = value.as_array().ok_or("expected ID array")?;
    let mut result = BTreeSet::new();
    for item in items {
        let id = item.as_str().ok_or("expected ID string")?;
        if id.is_empty() || !result.insert(id.to_owned()) {
            return Err(format!("empty or duplicate ID: {id}").into());
        }
    }
    Ok(result)
}
/// Digests of the LF and CRLF forms of a text file. Content changes still change both.
fn line_ending_digests(bytes: &[u8]) -> [String; 2] {
    let mut lf = Vec::with_capacity(bytes.len());
    let mut iter = bytes.iter().peekable();
    while let Some(&byte) = iter.next() {
        if byte == b'\r' && iter.peek() == Some(&&b'\n') {
            continue;
        }
        lf.push(byte);
    }
    let mut crlf = Vec::with_capacity(lf.len() + lf.len() / 32);
    for &byte in &lf {
        if byte == b'\n' {
            crlf.push(b'\r');
        }
        crlf.push(byte);
    }
    [digest(lf), digest(crlf)]
}
fn source(root: &Path, path: &str) -> Result<()> {
    if path.contains('\\')
        || path.split('/').any(|v| v == "..")
        || Path::new(path).is_absolute()
        || !root.join(path).is_file()
    {
        return Err(format!("invalid source path: {path}").into());
    }
    Ok(())
}

pub fn check(root: &Path) -> Result<()> {
    let inventory = read(root, "crates/racp-contract/migration-inventory.json")?;
    if inventory["format_version"] != 1 {
        return Err("unsupported inventory version".into());
    }
    let baseline_registry = inventory["baseline"]["registry"]
        .as_object()
        .ok_or("frozen registry missing")?;
    let baseline_catalog = inventory["baseline"]["catalog"]
        .as_array()
        .ok_or("frozen catalog missing")?;
    let baseline_ops: BTreeSet<String> = baseline_registry.keys().cloned().collect();
    let baseline_permissions: BTreeSet<String> = baseline_catalog
        .iter()
        .filter_map(|v| v["id"].as_str().map(str::to_owned))
        .collect();
    let expansion_ids: BTreeSet<String> = baseline_catalog
        .iter()
        .filter(|v| v["implementation"] == "planned")
        .map(|v| v["id"].as_str().unwrap().to_owned())
        .collect();
    if baseline_ops.len() != 100 || baseline_permissions.len() != 143 || expansion_ids.len() != 62 {
        return Err("frozen baseline set changed".into());
    }
    for input in inventory["baseline"]["inputs"]
        .as_array()
        .ok_or("missing baseline inputs")?
    {
        let path = input["path"].as_str().ok_or("missing input path")?;
        source(root, path)?;
        // Recorded hashes come from both LF and CRLF checkouts (core.autocrlf differs between
        // machines and CI runners), so accept either line-ending form of the same text.
        let current = line_ending_digests(&fs::read(root.join(path))?);
        let admitted: Vec<_> = inventory["deltas"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|delta| delta["path"] == path)
            .collect();
        if admitted.len() > 1 {
            return Err(format!("duplicate delta: {path}").into());
        }
        let explicit = admitted.first().is_some_and(|delta| {
            delta["baseline_sha256"] == input["sha256"]
                && delta["current_sha256"]
                    .as_str()
                    .is_some_and(|value| current.iter().any(|digest| digest == value))
                && delta["owner_task"].as_str().is_some_and(|s| !s.is_empty())
                && delta["reason"].as_str().is_some_and(|s| !s.is_empty())
        });
        let unchanged = input["sha256"]
            .as_str()
            .is_some_and(|value| current.iter().any(|digest| digest == value));
        if !unchanged && !explicit {
            return Err(format!(
                "baseline drift: {path}; record an explicit delta before updating inventory"
            )
            .into());
        }
    }
    let catalog = read(
        root,
        "packages/protocol/src/racp_protocol/permission_catalog.json",
    )?;
    let bindings = read(root, "crates/racp-core/src/permission-bindings.json")?;
    let operations = registry().as_object().ok_or("registry is not an object")?;
    let op_ids: BTreeSet<String> = operations.keys().cloned().collect();
    let mut expected_ops = baseline_ops.clone();
    for delta in inventory["operation_deltas"]
        .as_array()
        .ok_or("operation deltas missing")?
    {
        let id = delta["operation_id"]
            .as_str()
            .ok_or("operation delta ID missing")?;
        if delta["action"] != "add"
            || !expected_ops.insert(id.into())
            || delta["owner_task"]
                .as_str()
                .is_none_or(|s| !s.starts_with("R11-") && !s.starts_with("AS16-"))
        {
            return Err(format!("invalid operation delta: {id}").into());
        }
    }
    if expected_ops != op_ids {
        return Err("operation additions/removals lack an explicit delta".into());
    }
    let expected_permissions =
        admitted_permissions(&baseline_permissions, &inventory["permission_deltas"])?;
    let bound_ids: BTreeSet<String> = bindings
        .as_object()
        .ok_or("bindings are not an object")?
        .keys()
        .cloned()
        .collect();
    if bound_ids != op_ids {
        return Err("Registry / Rust binding operation set drift".into());
    }
    let catalog = catalog.as_array().ok_or("catalog is not an array")?;
    let mut permission_ids = BTreeSet::new();
    let mut categories = BTreeSet::new();
    let mut planned = BTreeSet::new();
    for permission in catalog {
        let id = permission["id"].as_str().ok_or("permission without ID")?;
        if !permission_ids.insert(id.to_owned()) {
            return Err(format!("duplicate catalog permission: {id}").into());
        }
        categories.insert(permission["category"].as_str().ok_or("category missing")?);
        let refs = strings(&permission["operations"])?;
        if !refs.is_subset(&op_ids) {
            return Err(format!("unregistered operation in {id}").into());
        }
        if permission["implementation"] == "planned" {
            planned.insert(id.to_owned());
        }
    }
    let mut seen_ops = BTreeSet::new();
    for entry in inventory["operations"]
        .as_array()
        .ok_or("operation inventory missing")?
    {
        let id = entry["operation_id"]
            .as_str()
            .ok_or("operation ID missing")?;
        if !seen_ops.insert(id.to_owned()) || !op_ids.contains(id) {
            return Err(format!("duplicate or unknown operation: {id}").into());
        }
        if entry["contract_digest"] != canonical_digest(&operations[id]) {
            return Err(format!("operation contract drift: {id}").into());
        }
        if strings(&entry["required_permission_ids"])? != strings(&bindings[id])? {
            return Err(format!("AND binding drift: {id}").into());
        }
        let expected: BTreeSet<String> = catalog
            .iter()
            .filter(|p| {
                p["operations"]
                    .as_array()
                    .is_some_and(|a| a.iter().any(|v| v == id))
            })
            .map(|p| p["id"].as_str().unwrap().to_owned())
            .collect();
        if strings(&entry["permission_ids"])? != expected || expected.is_empty() {
            return Err(format!("catalog binding drift or missing permission: {id}").into());
        }
        validate_entry(root, entry)?;
        let provider = fs::read_to_string(root.join(entry["source_path"].as_str().unwrap()))?;
        if !provider.contains(&format!("\"{id}\"")) {
            return Err(format!("Provider dispatch mapping not found: {id}").into());
        }
    }
    let mut seen_permissions = BTreeSet::new();
    let mut assigned_planned = BTreeSet::new();
    for entry in inventory["permissions"]
        .as_array()
        .ok_or("permission inventory missing")?
    {
        let id = entry["permission_id"]
            .as_str()
            .ok_or("permission ID missing")?;
        if !seen_permissions.insert(id.to_owned()) || !permission_ids.contains(id) {
            return Err(format!("duplicate or unknown permission: {id}").into());
        }
        let original = catalog.iter().find(|p| p["id"] == id).unwrap();
        if strings(&entry["operation_ids"])? != strings(&original["operations"])?
            || entry["category"] != original["category"]
            || entry["contract_digest"] != canonical_digest(original)
        {
            return Err(format!("permission contract drift: {id}").into());
        }
        if expansion_ids.contains(id) {
            if entry["baseline_kind"] != "expansion"
                || !entry["owner_task"]
                    .as_str()
                    .is_some_and(|s| s.starts_with("R11-"))
            {
                return Err(format!("unassigned expansion: {id}").into());
            }
            assigned_planned.insert(id.to_owned());
        }
        validate_entry(root, entry)?;
    }
    if seen_ops != op_ids
        || seen_permissions != permission_ids
        || assigned_planned != expansion_ids
        || permission_ids != expected_permissions
    {
        return Err("inventory omissions".into());
    }
    if permission_ids.len() != expected_permissions.len() || categories.len() != 18 {
        return Err("baseline counts changed; record an explicit delta".into());
    }
    for consumer in inventory["legacy_consumers"]
        .as_array()
        .ok_or("consumer inventory missing")?
    {
        if consumer["disposition"] == "retired_from_client_source"
            && root
                .join(
                    consumer["source_path"]
                        .as_str()
                        .ok_or("retired source path missing")?,
                )
                .is_file()
        {
            return Err("retired Client source unexpectedly present".into());
        }
        source(
            root,
            consumer
                .get("current_source_path")
                .unwrap_or(&consumer["source_path"])
                .as_str()
                .ok_or("consumer path missing")?,
        )?;
        if consumer["owner_task"].as_str().is_none_or(str::is_empty) {
            return Err("consumer owner missing".into());
        }
    }
    println!("static_pass: {} current operations / {} permissions / 18 categories / 62 assigned expansion IDs ({} still planned); frozen baseline 100 operations / 143 permissions preserved",op_ids.len(),permission_ids.len(),planned.len());
    Ok(())
}
fn validate_entry(root: &Path, entry: &Value) -> Result<()> {
    for field in [
        "owner_task",
        "backend",
        "availability_reason",
        "implementation_state",
        "evidence_ref",
        "blocking_dependency",
    ] {
        if entry[field].as_str().is_none_or(str::is_empty) {
            return Err(format!("missing {field}").into());
        }
    }
    source(
        root,
        entry["source_path"].as_str().ok_or("source path missing")?,
    )?;
    strings(&entry["platform"])?;
    strings(&entry["test_ids"])?;
    strings(&entry["cleanup_assertions"])?;
    Ok(())
}

fn admitted_permissions(baseline: &BTreeSet<String>, deltas: &Value) -> Result<BTreeSet<String>> {
    let mut expected_permissions = baseline.clone();
    for delta in deltas.as_array().into_iter().flatten() {
        let id = delta["permission_id"]
            .as_str()
            .ok_or("permission delta ID missing")?;
        if delta["action"] != "add"
            || !expected_permissions.insert(id.into())
            || delta["owner_task"]
                .as_str()
                .is_none_or(|s| !s.starts_with("AS16-"))
            || delta["reason"].as_str().is_none_or(str::is_empty)
        {
            return Err(format!("invalid permission delta: {id}").into());
        }
    }
    Ok(expected_permissions)
}

#[cfg(test)]
mod permission_delta_tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn permission_additions_require_explicit_unique_as16_delta_and_preserve_baseline() {
        let baseline = BTreeSet::from(["system.identity.read".to_owned()]);
        let addition = json!({"permission_id":"hardware.inventory.read","action":"add",
            "owner_task":"AS16-metadata","reason":"bounded metadata extension"});
        let admitted = admitted_permissions(&baseline, &json!([addition])).unwrap();
        assert!(admitted.contains("system.identity.read"));
        assert!(admitted.contains("hardware.inventory.read"));
        assert_ne!(
            admitted_permissions(&baseline, &Value::Null).unwrap(),
            admitted
        );
        assert_ne!(
            admitted,
            BTreeSet::from(["hardware.inventory.read".to_owned()])
        );
        for field in ["action", "owner_task", "reason", "permission_id"] {
            let mut invalid = addition.clone();
            invalid[field] = json!(if field == "permission_id" {
                "system.identity.read"
            } else {
                ""
            });
            assert!(admitted_permissions(&baseline, &json!([invalid])).is_err());
        }
        assert!(admitted_permissions(&baseline, &json!([addition, addition])).is_err());
    }
}
