use racp_contract::{canonical_digest, digest, timestamp, VERSION};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    process::{Command, Stdio},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub fn command(root: &Path, executable: &str, args: &[&str]) -> Result<String> {
    let output = Command::new(executable)
        .args(args)
        .current_dir(root)
        .stdin(Stdio::null())
        .output()?;
    if !output.status.success() {
        return Err(format!("build metadata command failed: {executable}").into());
    }
    if output.stdout.len() > 64 * 1024 * 1024 {
        return Err("build metadata too large".into());
    }
    Ok(String::from_utf8(output.stdout)?)
}
pub fn source(root: &Path) -> Result<Value> {
    let commit = command(root, "git", &["rev-parse", "HEAD"])?;
    let paths = command(
        root,
        "git",
        &[
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
    )?;
    let mut files = BTreeMap::new();
    for name in paths.split('\0').filter(|name| !name.is_empty()) {
        let allowed = [
            "crates/",
            "apps/client-native/",
            "apps/agent/",
            "xtask/",
            "docs/protocol/",
            "packages/protocol/",
        ]
        .iter()
        .any(|prefix| name.starts_with(prefix))
            || ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "LICENSE"].contains(&name);
        let extension = Path::new(name)
            .extension()
            .and_then(|v| v.to_str())
            .unwrap_or("");
        if allowed
            && (["rs", "toml", "lock", "json", "ico", "png", "py", "nsi"].contains(&extension)
                || name == "LICENSE")
        {
            let path = root.join(name);
            if path.is_file() {
                let mut file = racp_core::secure_read_file(&path)?;
                let (size, hash) = racp_core::distribution::hash_file(&mut file, 64 * 1024 * 1024)?;
                files.insert(name, json!({"size_bytes":size,"sha256":hash}));
            }
        }
    }
    let hashes = serde_json::to_value(files)?;
    Ok(
        json!({"commit":commit.trim(),"dirty":!command(root,"git",&["status","--porcelain"])?.is_empty(),
        "input_digest":canonical_digest(&hashes),"inputs":hashes}),
    )
}
pub fn sbom(root: &Path, cargo: &str) -> Result<Value> {
    sbom_for(
        root,
        cargo,
        "RACP Native Client",
        &["racp-agent", "racp-client-native"],
    )
}
pub fn sbom_for(root: &Path, cargo: &str, application: &str, roots: &[&str]) -> Result<Value> {
    let raw = command(
        root,
        cargo,
        &[
            "metadata",
            "--locked",
            "--format-version",
            "1",
            "--filter-platform",
            "x86_64-pc-windows-msvc",
        ],
    )?;
    let metadata: Value = serde_json::from_str(&raw)?;
    let packages = metadata["packages"]
        .as_array()
        .ok_or("Cargo metadata packages missing")?;
    let nodes = metadata["resolve"]["nodes"]
        .as_array()
        .ok_or("Cargo metadata graph missing")?;
    let mut reachable = BTreeSet::new();
    let mut pending: Vec<String> = packages
        .iter()
        .filter(|p| roots.contains(&p["name"].as_str().unwrap_or("")))
        .filter_map(|p| p["id"].as_str().map(str::to_owned))
        .collect();
    while let Some(id) = pending.pop() {
        if !reachable.insert(id.clone()) {
            continue;
        }
        let node = nodes
            .iter()
            .find(|n| n["id"] == id)
            .ok_or("Cargo dependency node missing")?;
        for dependency in node["deps"]
            .as_array()
            .ok_or("Cargo dependency edges missing")?
        {
            if dependency["dep_kinds"]
                .as_array()
                .is_some_and(|kinds| kinds.iter().any(|kind| kind["kind"] != "dev"))
            {
                pending.push(
                    dependency["pkg"]
                        .as_str()
                        .ok_or("Cargo dependency identity missing")?
                        .to_owned(),
                );
            }
        }
    }
    let components: Vec<Value> = packages.iter().filter(|p| p["id"].as_str().is_some_and(|id| reachable.contains(id)))
        .map(|p| json!({"type":"library","bom-ref":format!("cargo:{}@{}:{}",p["name"].as_str().unwrap_or(""),p["version"].as_str().unwrap_or(""),digest(p["source"].as_str().unwrap_or("workspace"))),"name":p["name"],"version":p["version"],
            "purl":format!("pkg:cargo/{}@{}",p["name"].as_str().unwrap_or(""),p["version"].as_str().unwrap_or("")),
            "properties":[{"name":"racp:cargo-license","value":p["license"].as_str().unwrap_or("UNKNOWN")},
                {"name":"racp:cargo-source","value":p["source"].as_str().unwrap_or("workspace")}]})).collect();
    Ok(
        json!({"bomFormat":"CycloneDX","specVersion":"1.6","version":1,
        "metadata":{"timestamp":timestamp(),"component":{"type":"application","name":application,"version":VERSION},
            "properties":[{"name":"racp:license-review","value":"pending"},{"name":"racp:scope","value":"Cargo native target graph; system DLLs and build-tool inventories require separate review"}]},
        "components":components,"properties":[{"name":"racp:cargo-metadata-sha256","value":digest(raw)}]}),
    )
}
