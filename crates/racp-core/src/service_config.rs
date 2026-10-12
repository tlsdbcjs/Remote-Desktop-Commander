//! Stopped-service CAS and durable config/ACL reconciliation. No remote operation.
use crate::{
    atomic_write, native_service::*, read_bounded, service_directory, state_schema::NativeState,
    InstanceLock, Workspaces,
};
use racp_contract::{digest, local_ipc::reject_duplicate_keys, RacpError};
use serde::{Deserialize, Serialize};
use std::path::Path;
pub const UPDATE_FILE: &str = "native-service-update.json";
pub const RESULT_FILE: &str = "native-service-config-result.json";
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Update {
    expected_revision: u64,
    expected_config_sha256: String,
    config: NativeServiceConfig,
}
/// SYSTEM-only delivery of a validated private input. Actual commit is still
/// performed by the virtual service actor through one-off SCM start parameters.
pub fn stage_admin(
    root: &Path,
    raw: &[u8],
    expected_staged: Option<&str>,
) -> Result<String, RacpError> {
    crate::machine_service::require_system()?;
    let directory = crate::machine_service::pin_private(root)?;
    let _config = InstanceLock::acquire(&root.join(CONFIG_LOCK))?;
    let _agent = InstanceLock::acquire(&root.join("native-agent.lock"))?;
    if root.join(CONFIG_PENDING).try_exists()? {
        return Err(RacpError::new("SERVICE_CONFIG_PENDING"));
    }
    if raw.is_empty() || raw.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    reject_duplicate_keys(raw)?;
    let mut update: Update = serde_json::from_slice(raw)?;
    update.config.validate(root)?;
    let old_raw = read_bounded(&root.join(CONFIG_FILE), 16384, false)?;
    reject_duplicate_keys(&old_raw)?;
    let mut old: NativeServiceConfig = serde_json::from_slice(&old_raw)?;
    old.validate(root)?;
    if update.expected_revision != old.revision
        || update.expected_config_sha256 != digest(&old_raw)
        || !compatible(&old, &update.config)
    {
        return Err(RacpError::new("STATE_REVISION_CONFLICT"));
    }
    if directory.info(UPDATE_FILE)?.is_some() {
        let actual = read_bounded(&root.join(UPDATE_FILE), 65536, false)?;
        if expected_staged != Some(digest(&actual).as_str()) {
            return Err(RacpError::new("SERVICE_CONFIG_INPUT_CONFLICT"));
        }
    } else if expected_staged.is_some() {
        return Err(RacpError::new("SERVICE_CONFIG_INPUT_CONFLICT"));
    }
    atomic_write(&root.join(UPDATE_FILE), raw, expected_staged.is_some())?;
    if read_bounded(&root.join(UPDATE_FILE), 65536, false)? != raw {
        return Err(RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"));
    }
    Ok(digest(raw))
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Pending {
    format_version: u32,
    input_sha256: String,
    old_sha256: String,
    old: NativeServiceConfig,
    next: NativeServiceConfig,
}
fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
}
fn same_path(a: &Path, b: &Path) -> bool {
    crate::path_within(a, b) && crate::path_within(b, a)
}
fn compatible(old: &NativeServiceConfig, next: &NativeServiceConfig) -> bool {
    old.revision.checked_add(1) == Some(next.revision)
        && same_path(&old.state_dir, &next.state_dir)
        && same_path(&old.endpoint_dir, &next.endpoint_dir)
        && old.scope.service_sid == next.scope.service_sid
}
fn authority(root: &Path) -> Result<(InstanceLock, InstanceLock), RacpError> {
    service_directory::require_service_actor()?;
    crate::verify_private_native_directory(root)?;
    NativeState::load(root)?;
    let config = InstanceLock::acquire(&root.join(CONFIG_LOCK))?;
    let agent = InstanceLock::acquire(&root.join("native-agent.lock"))?;
    Ok((config, agent))
}
pub fn update(root: &Path, input_hash: &str) -> Result<(), RacpError> {
    let _locks = authority(root)?;
    if root.join(CONFIG_PENDING).try_exists()? {
        return Err(RacpError::new("SERVICE_CONFIG_PENDING"));
    }
    let expected = input_hash.to_ascii_lowercase();
    if !hash(&expected) {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let raw = read_bounded(&root.join(UPDATE_FILE), 65536, false)?;
    if digest(&raw) != expected {
        return Err(RacpError::new("SERVICE_CONFIG_INPUT_CHANGED"));
    }
    reject_duplicate_keys(&raw)?;
    let mut input: Update = serde_json::from_slice(&raw)?;
    input.config.validate(root)?;
    let (old, old_hash) = NativeServiceConfig::read_for_change(root)?;
    if !hash(&input.expected_config_sha256)
        || input.expected_revision != old.revision
        || input.expected_config_sha256 != old_hash
        || !compatible(&old, &input.config)
    {
        return Err(RacpError::new("STATE_REVISION_CONFLICT"));
    }
    let _directory = service_directory::create(&old.endpoint_dir, &old.scope)?;
    let pending = Pending {
        format_version: 1,
        input_sha256: expected,
        old_sha256: old_hash,
        old,
        next: input.config,
    };
    let bytes = serde_json::to_vec(&pending)?;
    if bytes.len() > 65536 || serde_json::to_vec(&pending.next)?.len() > 16384 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    atomic_write(&root.join(CONFIG_PENDING), &bytes, false)?;
    finish(root, pending)
}
pub fn reconcile(root: &Path) -> Result<(), RacpError> {
    let _locks = authority(root)?;
    let raw = read_bounded(&root.join(CONFIG_PENDING), 65536, false)?;
    reject_duplicate_keys(&raw)?;
    let mut pending: Pending = serde_json::from_slice(&raw)?;
    pending.old.validate(root)?;
    pending.next.validate(root)?;
    if pending.format_version != 1
        || !hash(&pending.input_sha256)
        || !hash(&pending.old_sha256)
        || !compatible(&pending.old, &pending.next)
    {
        return Err(RacpError::new("SERVICE_CONFIG_CONFLICT"));
    }
    finish(root, pending)
}
fn finish(root: &Path, pending: Pending) -> Result<(), RacpError> {
    let next_bytes = serde_json::to_vec(&pending.next)?;
    let next_hash = digest(&next_bytes);
    let (actual, actual_hash) = NativeServiceConfig::read_for_change(root)?;
    if actual_hash == pending.old_sha256 && actual.revision == pending.old.revision {
        if serde_json::to_value(&actual)? != serde_json::to_value(&pending.old)? {
            return Err(RacpError::new("SERVICE_CONFIG_CONFLICT"));
        }
        if service_directory::pin(&pending.old.endpoint_dir, &pending.next.scope).is_err() {
            service_directory::change_scope(
                &pending.old.endpoint_dir,
                &pending.old.scope,
                &pending.next.scope,
            )?;
        }
        // Cooperative locks exclude native helpers; an external SYSTEM writer is
        // rechecked before commit and conflicts remain pending, not silently repaired.
        if NativeServiceConfig::read_for_change(root)?.1 != pending.old_sha256 {
            return Err(RacpError::new("SERVICE_CONFIG_CONFLICT"));
        }
        atomic_write(&root.join(CONFIG_FILE), &next_bytes, true)?;
    } else if actual_hash != next_hash || actual.revision != pending.next.revision {
        return Err(RacpError::new("SERVICE_CONFIG_CONFLICT"));
    }
    if service_directory::pin(&pending.next.endpoint_dir, &pending.next.scope).is_err() {
        service_directory::change_scope(
            &pending.next.endpoint_dir,
            &pending.old.scope,
            &pending.next.scope,
        )?;
    }
    if NativeServiceConfig::read_for_change(root)?.1 != next_hash {
        return Err(RacpError::new("SERVICE_CONFIG_CONFLICT"));
    }
    let receipt = serde_json::json!({"format_version":1,"input_sha256":pending.input_sha256,"config_sha256":next_hash,"revision":pending.next.revision,"committed":true,"acl_verified":true});
    atomic_write(
        &root.join(RESULT_FILE),
        &serde_json::to_vec(&receipt)?,
        true,
    )?;
    let raw = read_bounded(&root.join(CONFIG_PENDING), 65536, false)?;
    reject_duplicate_keys(&raw)?;
    let current: Pending = serde_json::from_slice(&raw)?;
    if serde_json::to_value(&current)? != serde_json::to_value(&pending)? {
        return Err(RacpError::new("SERVICE_CONFIG_CONFLICT"));
    }
    let guard = Workspaces::new(root, &[])?;
    guard
        .directory("default", root)?
        .unlink(CONFIG_PENDING, false)
}
