use racp_contract::{digest, hardware::*, new_id, timestamp};
use racp_core::{hardware::*, Journal, OutputSpool};
use racp_runtime::providers::hardware::*;
use std::time::Instant;
fn captured(bytes: &[u8]) -> (HardwareIoReceipt, HardwareCollectionManifest) {
    let caller = HardwareCaller {
        agent_device_id: "dev_pc".into(),
        agent_boot_id: "boot_test".into(),
        principal_id: "owner_one".into(),
        workspace_id: "default".into(),
    };
    let mut inventory = HardwareInventory::new("dev_pc", "boot_test").unwrap();
    let target = inventory
        .refresh(vec![ObservedInterface {
            device_key: "fixture-device".into(),
            interface_key: "fixture-interface".into(),
            vendor_id: Some(0x1234),
            product_id: Some(0x5678),
        }])
        .unwrap()
        .remove(0)
        .target;
    let mut sessions = HardwareSessions::new("dev_pc", "boot_test").unwrap();
    let now = Instant::now();
    let lease = sessions
        .reserve(caller.clone(), target.clone(), &inventory, 1000, now)
        .unwrap();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    let io = sessions
        .begin_io(
            &caller,
            &lease,
            &inventory,
            HardwareIoRequest {
                operation_id: new_id("op"),
                budget: HardwareIoBudget {
                    timeout_ms: 100,
                    max_bytes: 1024,
                },
                plan: HardwareReadPlan {
                    kind: CollectionKind::DescriptorSnapshot,
                    protocol_id: None,
                    method_revision: "fixture_v1".into(),
                },
            },
            now,
        )
        .unwrap();
    let receipt = sessions
        .finish_io(&caller, &lease, &inventory, &io, bytes.len() as u64, now)
        .unwrap();
    let manifest = HardwareCollectionManifest {
        version: 1,
        target,
        kind: CollectionKind::DescriptorSnapshot,
        protocol_id: None,
        method_revision: "fixture_v1".into(),
        captured_at: timestamp(),
        size_bytes: bytes.len() as u64,
        sha256: digest(bytes),
        partial: true,
        partial_reason: Some(PartialCollectionReason::UnsupportedField),
    };
    (receipt, manifest)
}
#[test]
fn fixture_collection_spools_provenance_and_bytes_together_and_survives_restart() {
    let root = tempfile::tempdir().unwrap();
    let db = root.path().join("journal.db");
    let dir = root.path().join("spool");
    let outputs = OutputSpool::new(dir.clone(), Journal::open(&db).unwrap()).unwrap();
    let (receipt, manifest) = captured(b"descriptor fixture \0\xff");
    let operation = receipt.operation_id().to_string();
    let output = spool_collection(
        &outputs,
        &operation,
        &receipt,
        manifest.clone(),
        b"descriptor fixture \0\xff",
    )
    .unwrap();
    let path = outputs.path(&output).unwrap();
    let raw = std::fs::read(&path).unwrap();
    assert_eq!(output["sha256"], digest(&raw));
    let (decoded, bytes) = decode_collection(&raw).unwrap();
    assert_eq!(decoded, manifest);
    assert_eq!(bytes, b"descriptor fixture \0\xff");
    assert!(decoded.partial);
    assert_eq!(decoded.kind, CollectionKind::DescriptorSnapshot);
    assert!(spool_collection(&outputs, &operation, &receipt, manifest.clone(), &bytes).is_err());
    assert_eq!(std::fs::read(&path).unwrap(), raw);
    drop(outputs);
    let outputs = OutputSpool::new(dir, Journal::open(&db).unwrap()).unwrap();
    assert_eq!(outputs.pending().unwrap().len(), 1);
    assert_eq!(outputs.descriptors(&operation).unwrap().len(), 1);
}
#[test]
fn collection_scope_hash_and_method_mismatch_leave_no_spool_attachment() {
    let root = tempfile::tempdir().unwrap();
    let outputs = OutputSpool::new(
        root.path().join("spool"),
        Journal::open(&root.path().join("journal.db")).unwrap(),
    )
    .unwrap();
    let (receipt, manifest) = captured(b"fixture");
    let mut foreign = manifest.clone();
    foreign.target.agent_device_id = "dev_other".into();
    assert!(spool_collection(
        &outputs,
        receipt.operation_id(),
        &receipt,
        foreign,
        b"fixture"
    )
    .is_err());
    let mut corrupt = manifest.clone();
    corrupt.sha256 = digest(b"other");
    assert!(spool_collection(
        &outputs,
        receipt.operation_id(),
        &receipt,
        corrupt,
        b"fixture"
    )
    .is_err());
    let mut misleading = manifest.clone();
    misleading.kind = CollectionKind::DocumentedReadProtocol;
    assert!(spool_collection(
        &outputs,
        receipt.operation_id(),
        &receipt,
        misleading,
        b"fixture"
    )
    .is_err());
    let mut replaced_method = manifest.clone();
    replaced_method.kind = CollectionKind::DocumentedReadProtocol;
    replaced_method.protocol_id = Some("different_protocol".into());
    assert!(spool_collection(
        &outputs,
        receipt.operation_id(),
        &receipt,
        replaced_method,
        b"fixture"
    )
    .is_err());
    assert!(spool_collection(
        &outputs,
        &new_id("op"),
        &receipt,
        manifest.clone(),
        b"fixture"
    )
    .is_err());
    assert!(spool_collection(&outputs, "../../escape", &receipt, manifest, b"fixture").is_err());
    assert!(outputs.pending().unwrap().is_empty());
    assert_eq!(std::fs::read_dir(outputs.root()).unwrap().count(), 0);
}
#[test]
fn host_decoder_rejects_tampered_bytes_duplicate_keys_and_raw_driver_inputs() {
    let (_, manifest) = captured(b"fixture");
    let raw = serde_json::to_vec(&serde_json::json!({"manifest":manifest,"payload_base64":"YmFk"}))
        .unwrap();
    assert!(decode_collection(&raw).is_err());
    assert!(decode_collection(br#"{"manifest":{},"manifest":{},"payload_base64":""}"#).is_err());
    assert!(decode_collection(br#"{"manifest":{},"payload_base64":"","os_handle":123}"#).is_err());
}
