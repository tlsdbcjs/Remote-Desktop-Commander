//! Explicit PnP worker boundary. No fixture switch, caller-selected executable,
//! device path input, driver IO or implicit startup discovery.
use racp_contract::RacpError;
use racp_core::hardware::{validate_observations, ObservedInterface};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    timeout_ms: u64,
}
impl Request {
    fn decode(raw: &[u8]) -> Result<Self, RacpError> {
        if raw.is_empty() || raw.len() > 128 {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        racp_contract::local_ipc::reject_duplicate_keys(raw)?;
        let request: Self = serde_json::from_slice(raw)?;
        if !(1..=30_000).contains(&request.timeout_ms) {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        Ok(request)
    }
}
// Worker-private IPC DTO: never used as a Gateway response or collection input.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Row {
    device_key: String,
    interface_key: String,
    vendor_id: Option<u16>,
    product_id: Option<u16>,
}
fn decode_snapshot(value: Value) -> Result<Vec<ObservedInterface>, RacpError> {
    let count = value
        .as_array()
        .ok_or_else(|| RacpError::new("HARDWARE_SNAPSHOT_INVALID"))?
        .len();
    if count > racp_contract::hardware::MAX_HARDWARE_INTERFACES {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let rows: Vec<Row> = serde_json::from_value(value)?;
    let rows: Vec<_> = rows
        .into_iter()
        .map(|r| ObservedInterface {
            device_key: r.device_key,
            interface_key: r.interface_key,
            vendor_id: r.vendor_id,
            product_id: r.product_id,
        })
        .collect();
    validate_observations(&rows)?;
    Ok(rows)
}
/// Future policy-authorized Provider calls this on a blocking task. The child
/// owns SetupAPI state; cancellation/deadline kills its entire contained tree.
pub fn discover(
    cwd: &Path,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<Vec<ObservedInterface>, RacpError> {
    let remaining = deadline
        .checked_duration_since(Instant::now())
        .ok_or_else(|| RacpError::new("TIMEOUT"))?;
    let timeout_ms = (remaining.as_millis().min(30_000)) as u64;
    if timeout_ms == 0 {
        return Err(RacpError::new("TIMEOUT"));
    }
    let value = crate::providers::worker::run(
        "hardware-discovery-worker",
        cwd,
        serde_json::json!({"timeout_ms":timeout_ms}),
        deadline,
        cancel,
    )?;
    decode_snapshot(value)
}
/// Agent's fixed command mode only. Tests call decoders and never this SDK path.
pub fn run() -> Result<(), RacpError> {
    let mut raw = vec![];
    std::io::stdin().take(129).read_to_end(&mut raw)?;
    let result = (|| {
        let request = Request::decode(&raw)?;
        let rows = super::enumerate_usb_interfaces(
            Instant::now() + Duration::from_millis(request.timeout_ms),
            &CancellationToken::new(),
        )?;
        validate_observations(&rows)?;
        Ok::<_, RacpError>(
            rows.into_iter()
                .map(|r| Row {
                    device_key: r.device_key,
                    interface_key: r.interface_key,
                    vendor_id: r.vendor_id,
                    product_id: r.product_id,
                })
                .collect::<Vec<_>>(),
        )
    })();
    let value = match result {
        Ok(rows) => serde_json::json!({"ok":true,"result":rows}),
        Err(error) => serde_json::json!({"ok":false,"error_code":error.code.0}),
    };
    let raw = serde_json::to_vec(&value)?;
    if raw.len() > 4 * 1024 * 1024 {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    std::io::stdout().write_all(&raw)?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn discovery_request_rejects_raw_paths_driver_actions_and_unbounded_budget() {
        Request::decode(br#"{"timeout_ms":1000}"#).unwrap();
        for raw in [
            br#"{"timeout_ms":0}"#.as_slice(),
            br#"{"timeout_ms":30001}"#,
            br#"{"timeout_ms":1,"timeout_ms":2}"#,
            br#"{"timeout_ms":1,"device_path":"arbitrary"}"#,
            br#"{"timeout_ms":1,"reset":true}"#,
        ] {
            assert!(Request::decode(raw).is_err());
        }
    }
    #[test]
    fn private_snapshot_rejects_duplicate_rows_and_unknown_handle_fields() {
        let row = serde_json::json!({"device_key":"fixture","interface_key":"fixture-interface","vendor_id":0x1234,"product_id":0x5678});
        assert_eq!(decode_snapshot(serde_json::json!([row])).unwrap().len(), 1);
        assert!(decode_snapshot(serde_json::json!([row, row])).is_err());
        let mut invalid = row;
        invalid["os_handle"] = serde_json::json!(123);
        assert!(decode_snapshot(serde_json::json!([invalid])).is_err());
        assert!(decode_snapshot(serde_json::json!({})).is_err());
        assert!(decode_snapshot(serde_json::json!(vec![serde_json::Value::Null; 257])).is_err());
    }
}
