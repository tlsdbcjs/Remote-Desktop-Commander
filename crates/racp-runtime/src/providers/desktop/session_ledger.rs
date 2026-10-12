//! Private service receipts bind recovery to the original bucket and OS process births.
use super::{native_identity::PinnedPeer, PairConfig, PeerIdentity};
use racp_contract::{local_ipc::reject_duplicate_keys, RacpError};
use racp_core::{native_service::NativeServiceConfig, Directory, Workspaces};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

const DIRECTORY: &str = "desktop-session-records";
pub const RESULT_FILE: &str = "native-service-session-recovery.json";
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Stamp {
    pid: u32,
    created: f64,
}
impl Stamp {
    fn from(peer: &PeerIdentity) -> Self {
        Self {
            pid: peer.pid,
            created: peer.created,
        }
    }
    fn valid(&self) -> bool {
        self.pid > 0 && self.created.is_finite() && self.created > 0.0
    }
    fn observe(&self) -> Result<Option<PinnedPeer>, RacpError> {
        match PinnedPeer::open(self.pid) {
            Ok(peer) if peer.identity().created == self.created => Ok(Some(peer)),
            Ok(_) => Ok(None), // PID reuse never authorizes a signal to the new process.
            Err(error) if error.code.0 == "PROCESS_NOT_FOUND" => Ok(None),
            Err(error) => Err(error),
        }
    }
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    format_version: u8,
    pair_id: String,
    user_sid: String,
    session_id: u32,
    agent: Stamp,
    launcher: Stamp,
    bucket_device: u64,
    bucket_inode: u64,
    broker: Option<Stamp>,
    guardian: Option<Stamp>,
    cleanup_confirmed: bool,
}
pub(super) struct Record {
    root: PathBuf,
    receipt: Receipt,
    last: Option<String>,
}
fn records(root: &Path) -> Result<Directory, RacpError> {
    racp_core::service_directory::require_service_actor()?;
    let root = root.join(DIRECTORY);
    racp_core::create_private_native_directory(&root)?;
    let guards = Workspaces::new(&root, &[])?;
    guards.directory("default", &root)
}
fn pair_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
impl Record {
    pub fn prepare(
        root: &Path,
        config: &PairConfig,
        caller: &PeerIdentity,
        bucket: &Directory,
    ) -> Result<Self, RacpError> {
        let directory = records(root)?;
        if directory.names()?.len() >= 64 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let info = bucket.own_info()?;
        let mut record = Self {
            root: root.to_owned(),
            last: None,
            receipt: Receipt {
                format_version: 1,
                pair_id: config.pair_id.clone(),
                user_sid: config.user_sid.clone(),
                session_id: config.session_id,
                agent: Stamp {
                    pid: config.agent_pid,
                    created: config.agent_created,
                },
                launcher: Stamp::from(caller),
                bucket_device: info.device,
                bucket_inode: info.inode,
                broker: None,
                guardian: None,
                cleanup_confirmed: false,
            },
        };
        record.save(false)?;
        Ok(record)
    }
    fn save(&mut self, overwrite: bool) -> Result<(), RacpError> {
        let directory = records(&self.root)?;
        let name = format!("{}.json", self.receipt.pair_id);
        if overwrite
            && self.last.as_ref()
                != Some(&racp_contract::digest(serde_json::to_vec(&read(
                    &directory, &name,
                )?)?))
        {
            return Err(RacpError::new("SERVICE_SESSION_RECEIPT_CHANGED"));
        }
        let bytes = serde_json::to_vec(&self.receipt)?;
        racp_core::atomic_write(
            &directory
                .path
                .join(format!("{}.json", self.receipt.pair_id)),
            &bytes,
            overwrite,
        )?;
        self.last = Some(racp_contract::digest(&bytes));
        Ok(())
    }
    pub fn registered(
        &mut self,
        broker: &PeerIdentity,
        guardian: &PeerIdentity,
    ) -> Result<(), RacpError> {
        self.receipt.broker = Some(Stamp::from(broker));
        self.receipt.guardian = Some(Stamp::from(guardian));
        self.save(true)
    }
    pub fn complete(&self) -> Result<(), RacpError> {
        let directory = records(&self.root)?;
        let name = format!("{}.json", self.receipt.pair_id);
        // Refuse a foreign/changed receipt instead of deleting by name alone.
        let current = read(&directory, &name)?;
        if serde_json::to_vec(&current)? != serde_json::to_vec(&self.receipt)? {
            return Err(RacpError::new("SERVICE_SESSION_RECEIPT_CHANGED"));
        }
        directory.unlink(&name, false)
    }
    pub fn confirm_cleanup(&mut self) -> Result<(), RacpError> {
        self.receipt.cleanup_confirmed = true;
        self.save(true)
    }
}
fn read(directory: &Directory, name: &str) -> Result<Receipt, RacpError> {
    use std::io::Read;
    let mut file = directory.open_pinned_read(name)?;
    let mut bytes = vec![];
    (&mut file).take(8193).read_to_end(&mut bytes)?;
    if bytes.len() > 8192 {
        return Err(RacpError::new("SERVICE_SESSION_RECEIPT_INVALID"));
    }
    reject_duplicate_keys(&bytes)?;
    let value: Receipt = serde_json::from_slice(&bytes)?;
    if value.format_version != 1
        || !pair_id(&value.pair_id)
        || name != format!("{}.json", value.pair_id)
        || !racp_contract::service_scope::interactive_user_sid(&value.user_sid)
        || value.session_id == 0
        || !value.agent.valid()
        || !value.launcher.valid()
        || value.broker.as_ref().is_some_and(|stamp| !stamp.valid())
        || value.guardian.as_ref().is_some_and(|stamp| !stamp.valid())
        || value.broker.is_some() != value.guardian.is_some()
    {
        return Err(RacpError::new("SERVICE_SESSION_RECEIPT_INVALID"));
    }
    Ok(value)
}

/// Explicit stopped-Agent service maintenance. Unknown launches remain recorded, never adopted.
pub fn recover(
    root: &Path,
    request_id: &str,
    cancel: tokio_util::sync::CancellationToken,
) -> Result<Value, RacpError> {
    if !request_id
        .strip_prefix("session_recovery_")
        .is_some_and(pair_id)
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    racp_core::service_directory::require_service_actor()?;
    let _config_lock =
        racp_core::InstanceLock::acquire(&root.join(racp_core::native_service::CONFIG_LOCK))?;
    let _agent_lock = racp_core::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    let config = NativeServiceConfig::load(root)?;
    let _control = racp_core::service_directory::pin(&config.endpoint_dir, &config.scope)?;
    let directory = records(root)?;
    let names = directory.names()?;
    if names.len() > 64 {
        return Err(RacpError::new("SERVICE_SESSION_RECEIPT_INVALID"));
    }
    let mut recovered = 0usize;
    let mut unresolved = vec![];
    let mut known = std::collections::BTreeSet::new();
    let deadline = Instant::now() + Duration::from_secs(60);
    for name in names {
        let receipt = read(&directory, &name)?;
        known.insert(receipt.pair_id.clone());
        let mut record = Record {
            root: root.to_owned(),
            last: Some(racp_contract::digest(serde_json::to_vec(&receipt)?)),
            receipt,
        };
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        match recover_one(&config, &mut record, deadline, &cancel) {
            Ok(()) => {
                record.complete()?;
                recovered += 1;
            }
            Err(error) => {
                unresolved.push(json!({"pair_id":record.receipt.pair_id,"error_code":error.code.0}))
            }
        }
    }
    let session_root = config.endpoint_dir.join("desktop-sessions");
    if session_root.try_exists()? {
        let sessions = racp_core::service_directory::sessions(&config.endpoint_dir, &config.scope)?;
        let names = sessions.names()?;
        if names.len() > 64 {
            return Err(RacpError::new("SERVICE_SESSION_RECEIPT_INVALID"));
        }
        for name in names {
            if !known.contains(&name) {
                unresolved.push(
                    json!({"pair_id":if pair_id(&name) {name.as_str()} else {"invalid"},
                    "error_code":"SERVICE_SESSION_BUCKET_UNRECORDED"}),
                );
            }
        }
    }
    let result = json!({"format_version":1,"request_id":request_id,"config_sha256":racp_contract::digest(
        racp_core::read_bounded(&root.join(racp_core::native_service::CONFIG_FILE),16384,false)?),
        "recovered":recovered,"unresolved":unresolved,"complete":unresolved.is_empty(),
        "credential_accessed":false,"processes_terminated":false});
    racp_core::atomic_write(&root.join(RESULT_FILE), &serde_json::to_vec(&result)?, true)?;
    if !unresolved.is_empty() {
        return Err(RacpError::new("SERVICE_SESSION_RECOVERY_INCOMPLETE"));
    }
    Ok(result)
}
fn recover_one(
    config: &NativeServiceConfig,
    record: &mut Record,
    budget: Instant,
    cancel: &tokio_util::sync::CancellationToken,
) -> Result<(), RacpError> {
    if Instant::now() >= budget {
        return Err(RacpError::new("SERVICE_SESSION_RECOVERY_BUDGET_EXHAUSTED"));
    }
    let value = &record.receipt;
    if value.agent.observe()?.is_some() {
        return Err(RacpError::new("SERVICE_SESSION_AGENT_STILL_RUNNING"));
    }
    let root = config
        .endpoint_dir
        .join("desktop-sessions")
        .join(&value.pair_id);
    if !root.try_exists()? {
        if !value.cleanup_confirmed {
            return Err(RacpError::new("SERVICE_SESSION_INPUT_CLEANUP_UNKNOWN"));
        }
        if value
            .broker
            .as_ref()
            .map(Stamp::observe)
            .transpose()?
            .flatten()
            .is_some()
            || value
                .guardian
                .as_ref()
                .map(Stamp::observe)
                .transpose()?
                .flatten()
                .is_some()
        {
            return Err(RacpError::new("SERVICE_SESSION_BUCKET_MISSING"));
        }
        return Ok(());
    }
    let bucket =
        racp_core::service_directory::pin_pair(&root, &config.scope.service_sid, &value.user_sid)?;
    let identity = bucket.own_info()?;
    if (identity.device, identity.inode) != (value.bucket_device, value.bucket_inode) {
        return Err(RacpError::new("SERVICE_SESSION_BUCKET_CHANGED"));
    }
    if value.broker.is_none() {
        if value.launcher.observe()?.is_none() && bucket.names()?.is_empty() {
            record.confirm_cleanup()?;
            return super::service_session::cleanup_bucket(bucket);
        }
        racp_core::atomic_write(&root.join("guardian-stop"), b"", true)?;
        return Err(RacpError::new("SERVICE_SESSION_LAUNCH_OUTCOME_UNKNOWN"));
    }
    let broker = value.broker.as_ref().unwrap().observe()?;
    let guardian = value.guardian.as_ref().unwrap().observe()?;
    for peer in [&broker, &guardian].into_iter().flatten() {
        if peer.identity().sid != value.user_sid || peer.identity().session != value.session_id {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
    }
    if guardian.is_some() {
        racp_core::atomic_write(&root.join("guardian-stop"), b"", true)?;
    }
    let deadline = (Instant::now() + Duration::from_secs(5)).min(budget);
    while !broker
        .as_ref()
        .map(PinnedPeer::exited)
        .transpose()?
        .unwrap_or(true)
        || !guardian
            .as_ref()
            .map(PinnedPeer::exited)
            .transpose()?
            .unwrap_or(true)
    {
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("SERVICE_SESSION_CLEANUP_PENDING"));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    if let Some(peer) = &guardian {
        if !peer.exit_success()? {
            return Err(RacpError::new("SERVICE_SESSION_INPUT_CLEANUP_UNKNOWN"));
        }
    }
    // This status is not a substitute for the OS death checks above; mismatches retain the bucket.
    if value.cleanup_confirmed {
        return super::service_session::cleanup_bucket(bucket);
    }
    let bytes = racp_core::read_bounded(&root.join("guardian-status.json"), 4096, false)?;
    reject_duplicate_keys(&bytes)?;
    let status: Value = serde_json::from_slice(&bytes)?;
    let expected = value.guardian.as_ref().unwrap();
    if status["pid"] != expected.pid
        || status["create_time"] != expected.created
        || status["cleanup_confirmed"] != true
        || status["healthy"] != true
        || status["held_count"] != 0
    {
        return Err(RacpError::new("SERVICE_SESSION_INPUT_CLEANUP_UNKNOWN"));
    }
    record.confirm_cleanup()?;
    super::service_session::cleanup_bucket(bucket)
}
