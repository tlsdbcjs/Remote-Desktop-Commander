//! Metadata Provider and internal acquisition artifact bridge. Device open/read
//! and collection RPC admission remain unimplemented.
use base64::{engine::general_purpose::STANDARD, Engine};
use racp_contract::{hardware::*, new_id, RacpError};
use racp_core::{hardware::HardwareIoReceipt, OutputSpool};
use serde::{Deserialize, Serialize};
use serde_json::Value;
const MAX_ENVELOPE_BYTES: usize = 24 * 1024 * 1024;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CollectionBundle {
    manifest: HardwareCollectionManifest,
    payload_base64: String,
}

/// One attachment keeps bytes and provenance inseparable in the existing
/// resumable artifact pipeline. Caller must still enforce the emission fence.
pub fn spool_collection(
    outputs: &OutputSpool,
    operation_id: &str,
    receipt: &HardwareIoReceipt,
    manifest: HardwareCollectionManifest,
    bytes: &[u8],
) -> Result<Value, RacpError> {
    if !opaque_id(operation_id, "op_")
        || operation_id != receipt.operation_id()
        || manifest.target != *receipt.target()
        || manifest.size_bytes != receipt.byte_count()
        || manifest.kind != receipt.read_plan().kind
        || manifest.protocol_id != receipt.read_plan().protocol_id
        || manifest.method_revision != receipt.read_plan().method_revision
    {
        return Err(RacpError::new("HARDWARE_COLLECTION_INVALID"));
    }
    manifest.validate_bytes(bytes)?;
    let reservation = new_id("hwartifact_reservation");
    outputs.reserve(
        &reservation,
        "internal.hardware.collection",
        &serde_json::json!({}),
    )?;
    let result = (|| {
        let bundle = CollectionBundle {
            manifest,
            payload_base64: STANDARD.encode(bytes),
        };
        let raw = serde_json::to_vec(&bundle)?;
        if raw.len() > MAX_ENVELOPE_BYTES {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let path = outputs
            .root()
            .join(format!("{}.hardware.json", receipt.io_id()));
        racp_core::atomic_write(&path, &raw, false)?;
        outputs.register(&path, operation_id, "application/json")
    })();
    outputs.release(&reservation)?;
    result
}
/// Static-analysis host can verify and extract the captured bytes without a
/// hardware connection. It cannot turn this manifest into native IO authority.
pub fn decode_collection(raw: &[u8]) -> Result<(HardwareCollectionManifest, Vec<u8>), RacpError> {
    if raw.len() > MAX_ENVELOPE_BYTES {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    racp_contract::local_ipc::reject_duplicate_keys(raw)?;
    let bundle: CollectionBundle = serde_json::from_slice(raw)?;
    bundle.manifest.validate()?;
    if bundle.payload_base64.len() > ((MAX_HARDWARE_IO_BYTES as usize).div_ceil(3) * 4) {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let bytes = STANDARD
        .decode(&bundle.payload_base64)
        .map_err(|_| RacpError::new("HARDWARE_COLLECTION_INVALID"))?;
    bundle.manifest.validate_bytes(&bytes)?;
    Ok((bundle.manifest, bytes))
}
mod provider;
pub use provider::Hardware;
