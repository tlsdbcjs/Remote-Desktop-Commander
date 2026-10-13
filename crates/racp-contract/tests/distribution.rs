use racp_contract::distribution::NativePayload;
use serde_json::{json, Value};

fn service() -> Value {
    json!({
        "format_version": 1, "product_version": racp_contract::VERSION,
        "kind": "native-agent-service", "platform": "win", "architecture": "x64",
        "files": (["LICENSE", "racp-agent.exe", "sbom.json"].map(|path| json!({
            "path": path, "size_bytes": 100, "sha256": "a".repeat(64)
        })))
    })
}

#[test]
fn agent_service_admits_only_agent_license_and_sbom() {
    let manifest: NativePayload = serde_json::from_value(service()).unwrap();
    manifest.validate().unwrap();
    for path in [
        "racp-client.exe",
        "credential.bin",
        "../racp-agent.exe",
        "runtime.exe",
    ] {
        let mut value = service();
        value["files"][1]["path"] = json!(path);
        let manifest: NativePayload = serde_json::from_value(value).unwrap();
        assert!(manifest.validate().is_err(), "admitted {path}");
    }
}

#[test]
fn client_manifest_without_kind_keeps_client_required() {
    let mut value = service();
    value.as_object_mut().unwrap().remove("kind");
    let manifest: NativePayload = serde_json::from_value(value.clone()).unwrap();
    assert!(manifest.validate().is_err());
    value["files"].as_array_mut().unwrap().push(json!({
        "path": "racp-client.exe", "size_bytes": 100, "sha256": "b".repeat(64)
    }));
    let manifest: NativePayload = serde_json::from_value(value).unwrap();
    manifest.validate().unwrap();
    assert!(serde_json::to_value(manifest)
        .unwrap()
        .get("kind")
        .is_none());
}

#[test]
fn service_manifest_rejects_unknown_kind_fields_and_invalid_entries() {
    for kind in [
        "native-agent",
        "native-client-service",
        "",
        "linux-agent-service",
    ] {
        let mut value = service();
        value["kind"] = json!(kind);
        assert!(serde_json::from_value::<NativePayload>(value).is_err());
    }
    let mut value = service();
    value["credential"] = json!("must_not_be_admitted");
    assert!(serde_json::from_value::<NativePayload>(value).is_err());
    for (field, bad) in [
        ("platform", json!("linux")),
        ("architecture", json!("arm64")),
        ("format_version", json!(2)),
        ("product_version", json!("99.0.0")),
    ] {
        let mut value = service();
        value[field] = bad;
        assert!(serde_json::from_value::<NativePayload>(value)
            .unwrap()
            .validate()
            .is_err());
    }
    for (field, bad) in [
        ("size_bytes", json!(0)),
        ("size_bytes", json!(u64::MAX)),
        ("sha256", json!("A".repeat(64))),
        ("sha256", json!("a".repeat(63))),
        ("path", json!("LICENSE")),
    ] {
        let mut value = service();
        value["files"][1][field] = bad;
        assert!(serde_json::from_value::<NativePayload>(value)
            .unwrap()
            .validate()
            .is_err());
    }
}
