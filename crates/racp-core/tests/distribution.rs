#![cfg(all(windows, target_arch = "x86_64"))]
use racp_core::distribution::{pin_agent_payload, pin_agent_service_payload, pin_payload};
use serde_json::{json, Value};
use std::{fs, path::Path};
use tempfile::{tempdir, TempDir};

fn pe(machine: u16) -> Vec<u8> {
    let mut bytes = vec![0; 90];
    bytes[..2].copy_from_slice(b"MZ");
    bytes[60..64].copy_from_slice(&64u32.to_le_bytes());
    bytes[64..68].copy_from_slice(b"PE\0\0");
    bytes[68..70].copy_from_slice(&machine.to_le_bytes());
    bytes[88..90].copy_from_slice(&0x20bu16.to_le_bytes());
    bytes
}
fn fixture(service: bool, machine: u16) -> (TempDir, Value) {
    let root = tempdir().unwrap();
    let mut files = vec![];
    for (name, bytes) in [
        ("LICENSE", b"fixture license".to_vec()),
        ("racp-agent.exe", pe(machine)),
        ("sbom.json", b"{}".to_vec()),
    ] {
        fs::write(root.path().join(name), &bytes).unwrap();
        files.push(
            json!({"path":name,"size_bytes":bytes.len(),"sha256":racp_contract::digest(&bytes)}),
        );
    }
    if !service {
        let bytes = pe(machine);
        fs::write(root.path().join("racp-client.exe"), &bytes).unwrap();
        files.push(json!({"path":"racp-client.exe","size_bytes":bytes.len(),"sha256":racp_contract::digest(bytes)}));
    }
    let mut manifest = json!({"format_version":1,"product_version":racp_contract::VERSION,
        "platform":"win","architecture":"x64","files":files});
    if service {
        manifest["kind"] = json!("native-agent-service");
    }
    save(root.path(), &manifest);
    (root, manifest)
}
fn save(root: &Path, value: &Value) {
    fs::write(
        root.join("payload-manifest.json"),
        serde_json::to_vec(value).unwrap(),
    )
    .unwrap();
}
fn service_error(root: &Path) -> &'static str {
    pin_agent_service_payload(root)
        .err()
        .expect("unsafe payload admitted")
        .code
        .0
}

#[test]
fn gui_free_payload_admitted_and_client_role_cannot_be_substituted() {
    let (service, _) = fixture(true, 0x8664);
    assert_eq!(
        pin_agent_service_payload(service.path())
            .unwrap()
            .manifest
            .files
            .len(),
        3
    );
    assert!(pin_agent_payload(service.path()).is_ok());
    assert_eq!(
        pin_payload(service.path()).err().unwrap().code.0,
        "PACKAGE_KIND_MISMATCH"
    );
    let (client, _) = fixture(false, 0x8664);
    assert!(pin_payload(client.path()).is_ok());
    assert!(pin_agent_payload(client.path()).is_ok());
    assert_eq!(service_error(client.path()), "PACKAGE_KIND_MISMATCH");
}
#[test]
fn service_hash_target_and_file_set_are_enforced() {
    let (root, _) = fixture(true, 0x8664);
    fs::write(root.path().join("racp-agent.exe"), b"changed").unwrap();
    assert_eq!(service_error(root.path()), "PACKAGE_HASH_MISMATCH");
    let (root, _) = fixture(true, 0xaa64);
    assert_eq!(service_error(root.path()), "RUNTIME_PLATFORM_MISMATCH");
    for name in ["credential.bin", "racp-client.exe", "runtime"] {
        let (root, _) = fixture(true, 0x8664);
        fs::write(root.path().join(name), b"unlisted").unwrap();
        assert_eq!(service_error(root.path()), "PACKAGE_INVALID");
    }
    let (root, _) = fixture(true, 0x8664);
    fs::remove_file(root.path().join("sbom.json")).unwrap();
    assert_eq!(service_error(root.path()), "PACKAGE_INVALID");
}
#[test]
fn duplicate_manifest_keys_and_hard_links_are_rejected() {
    let (root, manifest) = fixture(true, 0x8664);
    let raw = serde_json::to_string(&manifest).unwrap();
    fs::write(
        root.path().join("payload-manifest.json"),
        raw.replacen('{', "{\"kind\":\"native-client\",", 1),
    )
    .unwrap();
    assert!(pin_agent_service_payload(root.path()).is_err());
    save(root.path(), &manifest);
    let outside = tempdir().unwrap();
    fs::hard_link(
        root.path().join("racp-agent.exe"),
        outside.path().join("alias.exe"),
    )
    .unwrap();
    assert!(pin_agent_service_payload(root.path()).is_err());
}
#[test]
fn admitted_service_files_and_manifest_remain_pinned() {
    let (root, _) = fixture(true, 0x8664);
    let pins = pin_agent_service_payload(root.path()).unwrap();
    for name in [
        "racp-agent.exe",
        "LICENSE",
        "sbom.json",
        "payload-manifest.json",
    ] {
        assert!(fs::OpenOptions::new()
            .write(true)
            .open(root.path().join(name))
            .is_err());
    }
    drop(pins);
    assert!(fs::OpenOptions::new()
        .write(true)
        .open(root.path().join("racp-agent.exe"))
        .is_ok());
}
