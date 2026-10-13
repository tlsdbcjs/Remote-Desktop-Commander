use racp_contract::{hardware::*, new_id};
use serde_json::json;
fn target() -> HardwareTarget {
    HardwareTarget {
        agent_device_id: "dev_fixture".into(),
        agent_boot_id: "boot_fixture".into(),
        hardware_id: new_id("hardware"),
        interface_id: new_id("hwif"),
        connection_generation: new_id("connection"),
    }
}
#[test]
fn hardware_identity_rejects_pc_substitution_raw_paths_and_unknown_fields() {
    let mut t = target();
    t.validate().unwrap();
    t.hardware_id = t.agent_device_id.clone();
    assert!(t.validate().is_err());
    t = target();
    t.interface_id = r"\\?\usb#vid_1234".into();
    assert!(t.validate().is_err());
    let mut value = serde_json::to_value(target()).unwrap();
    value["os_handle"] = json!(42);
    assert!(serde_json::from_value::<HardwareTarget>(value).is_err());
    let mut value = serde_json::to_value(target()).unwrap();
    value["device_path"] = json!(r"\\?\usb#device");
    assert!(serde_json::from_value::<HardwareTarget>(value).is_err());
}
#[test]
fn hardware_lease_and_io_budgets_are_bounded_typed_inputs() {
    let mut lease = HardwareLease {
        session_id: new_id("hwsession"),
        lease_id: new_id("hwlease"),
        target: target(),
    };
    lease.validate().unwrap();
    lease.lease_id = lease.session_id.clone();
    assert!(lease.validate().is_err());
    for (timeout_ms, max_bytes) in [(0, 1), (30_001, 1), (1, 0), (1, MAX_HARDWARE_IO_BYTES + 1)] {
        assert!(HardwareIoBudget {
            timeout_ms,
            max_bytes
        }
        .validate()
        .is_err());
    }
    HardwareIoBudget {
        timeout_ms: 30_000,
        max_bytes: MAX_HARDWARE_IO_BYTES,
    }
    .validate()
    .unwrap();
    assert!(serde_json::from_value::<HardwareIoBudget>(
        json!({"timeout_ms":1,"max_bytes":1,"request_type":0x80})
    )
    .is_err());
}
#[test]
fn collection_manifest_cannot_claim_unqualified_firmware_read_or_hide_partial_capture() {
    let mut manifest = HardwareCollectionManifest {
        version: 1,
        target: target(),
        kind: CollectionKind::DescriptorSnapshot,
        protocol_id: None,
        method_revision: "metadata_v1".into(),
        captured_at: racp_contract::timestamp(),
        size_bytes: 3,
        sha256: racp_contract::digest(b"usb"),
        partial: true,
        partial_reason: Some(PartialCollectionReason::UnsupportedField),
    };
    manifest.validate_bytes(b"usb").unwrap();
    manifest.partial = false;
    assert!(manifest.validate().is_err());
    manifest.partial_reason = None;
    manifest.kind = CollectionKind::DocumentedReadProtocol;
    assert!(manifest.validate().is_err());
    manifest.protocol_id = Some("vendor_protocol_v1".into());
    manifest.validate_bytes(b"usb").unwrap();
    assert!(manifest.validate_bytes(b"bad").is_err());
    let mut raw = serde_json::to_value(manifest).unwrap();
    raw["kind"] = json!("generic_firmware_dump");
    assert!(serde_json::from_value::<HardwareCollectionManifest>(raw).is_err());
}
