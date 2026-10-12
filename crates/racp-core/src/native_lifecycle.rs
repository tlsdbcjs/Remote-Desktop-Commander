//! Last native execution receipt. An unlocked Agent is not proof that its resources retired.
use crate::{atomic_write, read_bounded, state_schema::NativeState, InstanceLock};
use racp_contract::{local_ipc::reject_duplicate_keys, new_id, RacpError};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

const FILE: &str = "native-run.json";
#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Phase {
    NeverStarted,
    Starting,
    Running,
    Complete,
    Failed,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    schema_version: u32,
    device_id: String,
    run_id: Option<String>,
    boot_id: Option<String>,
    phase: Phase,
    error_code: Option<String>,
}
fn write(root: &Path, receipt: &Receipt, overwrite: bool) -> Result<(), RacpError> {
    crate::verify_private_native_directory(root)?;
    atomic_write(&root.join(FILE), &serde_json::to_vec(receipt)?, overwrite)
}
/// Registration calls this before publishing native-state.json, while holding its reservation.
pub(crate) fn initialize(root: &Path, state: &NativeState) -> Result<(), RacpError> {
    crate::native_release::publish(root)?;
    write(
        root,
        &Receipt {
            schema_version: 1,
            device_id: state.device_id.clone(),
            run_id: None,
            boot_id: None,
            phase: Phase::NeverStarted,
            error_code: None,
        },
        false,
    )
}
pub struct NativeRun {
    root: PathBuf,
    receipt: Receipt,
}
impl NativeRun {
    /// The caller must hold native-agent.lock throughout this execution.
    /// Commit the uncertain start BEFORE creating runtime/provider processes.
    pub fn begin(root: &Path, state: &NativeState) -> Result<Self, RacpError> {
        crate::native_release::publish(root)?;
        let run = Self {
            root: root.to_owned(),
            receipt: Receipt {
                schema_version: 1,
                device_id: state.device_id.clone(),
                run_id: Some(new_id("run")),
                boot_id: None,
                phase: Phase::Starting,
                error_code: None,
            },
        };
        write(root, &run.receipt, true)?;
        Ok(run)
    }
    pub fn running(&mut self, boot_id: &str) -> Result<(), RacpError> {
        self.receipt.boot_id = Some(boot_id.to_owned());
        self.receipt.phase = Phase::Running;
        write(&self.root, &self.receipt, true)
    }
    pub fn finish(mut self, result: Result<(), RacpError>) -> Result<(), RacpError> {
        self.receipt.phase = if result.is_ok() {
            Phase::Complete
        } else {
            Phase::Failed
        };
        self.receipt.error_code = result.err().map(|error| error.code.0.to_owned());
        write(&self.root, &self.receipt, true)
            .map_err(|_| RacpError::new("NATIVE_CLEANUP_UNCONFIRMED"))
    }
}
/// Holds the same startup lock while checking the durable receipt. No credential read.
pub fn stopped_cleanly(root: &Path) -> Result<bool, RacpError> {
    let _lock = pin_stopped(root)?;
    Ok(true)
}
/// Retain the startup lock during an explicit local maintenance inspection.
pub fn pin_stopped(root: &Path) -> Result<InstanceLock, RacpError> {
    crate::verify_private_native_directory(root)?;
    let lock = InstanceLock::acquire(&root.join("native-agent.lock"))?;
    let state = NativeState::load(root)?;
    if !root.join(FILE).try_exists()? {
        return Err(RacpError::new("NATIVE_CLEANUP_UNCONFIRMED"));
    }
    let raw = read_bounded(&root.join(FILE), 4096, true)?;
    reject_duplicate_keys(&raw)?;
    let receipt: Receipt = serde_json::from_slice(&raw)?;
    let identifier = |text: &str| {
        !text.is_empty()
            && text.len() <= 96
            && text
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
    };
    let initial = receipt.phase == Phase::NeverStarted;
    if receipt.schema_version != 1
        || receipt.device_id != state.device_id
        || !identifier(&receipt.device_id)
        || (initial
            && (receipt.run_id.is_some()
                || receipt.boot_id.is_some()
                || receipt.error_code.is_some()))
        || (!initial && !receipt.run_id.as_deref().is_some_and(identifier))
        || (receipt.phase == Phase::Complete
            && (!receipt.boot_id.as_deref().is_some_and(identifier)
                || receipt.error_code.is_some()))
    {
        return Err(RacpError::new("NATIVE_CLEANUP_UNCONFIRMED"));
    }
    if matches!(receipt.phase, Phase::NeverStarted | Phase::Complete) {
        Ok(lock)
    } else {
        Err(RacpError::new("NATIVE_CLEANUP_UNCONFIRMED"))
    }
}
