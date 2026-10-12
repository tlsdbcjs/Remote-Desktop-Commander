//! Explicit SCM bootstrap under the fixed virtual account. No GUI token transport.
use racp_contract::{digest, local_ipc::reject_duplicate_keys, RacpError};
use racp_core::{
    native_service::*,
    state_schema::{NativeState, STATE_FILE},
    Directory, FileInfo, Workspaces,
};
use serde::{Deserialize, Serialize};
use std::{
    fs::File,
    io::Read,
    os::windows::{fs::OpenOptionsExt, io::AsRawHandle},
    path::Path,
};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::Storage::FileSystem::*;
const CLEANUP: &str = "native-service-bootstrap-cleanup.json";
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cleanup {
    format_version: u32,
    device_id: String,
    bootstrap_sha256: String,
    bootstrap_revision: String,
    connection_sha256: String,
    connection_revision: String,
}
struct Input {
    _file: File,
    raw: Vec<u8>,
    revision: String,
}
fn input(dir: &Directory, name: &str) -> Result<Input, RacpError> {
    let mut file = dir.open_pinned_read(name)?;
    let revision = FileInfo::from_file(&file)?.revision();
    let mut raw = vec![];
    (&mut file).take(65537).read_to_end(&mut raw)?;
    if raw.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    Ok(Input {
        _file: file,
        raw,
        revision,
    })
}
fn delete(
    dir: &Directory,
    name: &str,
    expected_hash: &str,
    expected_revision: &str,
) -> Result<(), RacpError> {
    if dir.info(name)?.is_none() {
        return Ok(());
    }
    let mut file = std::fs::OpenOptions::new()
        .access_mode(FILE_GENERIC_READ | DELETE)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(dir.path.join(name))?;
    let metadata = FileInfo::from_file(&file)?;
    if metadata.directory || metadata.link || metadata.revision() != expected_revision {
        return Err(RacpError::new("SERVICE_BOOTSTRAP_CLEANUP_CONFLICT"));
    }
    let mut raw = vec![];
    (&mut file).take(65537).read_to_end(&mut raw)?;
    if raw.len() > 65536 || digest(&raw) != expected_hash {
        return Err(RacpError::new("SERVICE_BOOTSTRAP_CLEANUP_CONFLICT"));
    }
    let info = FILE_DISPOSITION_INFO { DeleteFile: true };
    if unsafe {
        SetFileInformationByHandle(
            file.as_raw_handle(),
            FileDispositionInfo,
            (&info as *const FILE_DISPOSITION_INFO).cast(),
            std::mem::size_of_val(&info) as u32,
        )
    } == 0
    {
        return Err(RacpError::new("SERVICE_BOOTSTRAP_CLEANUP_UNKNOWN"));
    }
    drop(file);
    if dir.info(name)?.is_some() {
        return Err(RacpError::new("SERVICE_BOOTSTRAP_CLEANUP_UNKNOWN"));
    }
    Ok(())
}
fn finish(root: &Path, dir: &Directory) -> Result<(), RacpError> {
    let pending = input(dir, CLEANUP)?;
    reject_duplicate_keys(&pending.raw)?;
    let cleanup: Cleanup = serde_json::from_slice(&pending.raw)?;
    let hash_valid = |value: &str| {
        value.len() == 64
            && value
                .bytes()
                .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    };
    let revision_valid = |value: &str| {
        !value.is_empty()
            && value.len() <= 128
            && value.split('-').count() == 4
            && value
                .split('-')
                .all(|part| !part.is_empty() && part.bytes().all(|v| v.is_ascii_hexdigit()))
    };
    if cleanup.format_version != 1
        || cleanup.device_id != NativeState::load(root)?.device_id
        || !hash_valid(&cleanup.bootstrap_sha256)
        || !hash_valid(&cleanup.connection_sha256)
        || !revision_valid(&cleanup.bootstrap_revision)
        || !revision_valid(&cleanup.connection_revision)
    {
        return Err(RacpError::new("SERVICE_BOOTSTRAP_CLEANUP_CONFLICT"));
    }
    delete(
        dir,
        BOOTSTRAP_FILE,
        &cleanup.bootstrap_sha256,
        &cleanup.bootstrap_revision,
    )?;
    delete(
        dir,
        CONNECTION_FILE,
        &cleanup.connection_sha256,
        &cleanup.connection_revision,
    )?;
    let hash = digest(&pending.raw);
    let revision = pending.revision.clone();
    drop(pending);
    delete(dir, CLEANUP, &hash, &revision)
}
pub async fn prepare(
    root: &Path,
    bootstrap_hash: Option<&str>,
    cancel: &CancellationToken,
) -> Result<(), RacpError> {
    let config = NativeServiceConfig::load_inputs(root)?;
    crate::local_ipc::LocalEndpoint::for_service(root, config.scope)?;
    let guards = Workspaces::new(root, &[])?;
    let dir = guards.directory("default", root)?;
    if dir.info(STATE_FILE)?.is_some() {
        NativeState::load(root)?;
        if dir.info(CLEANUP)?.is_some() {
            finish(root, &dir)?;
        }
        return Ok(());
    }
    let hash = bootstrap_hash
        .filter(|hash| hash.len() == 64 && hash.bytes().all(|v| v.is_ascii_hexdigit()))
        .ok_or_else(|| RacpError::new("SERVICE_BOOTSTRAP_REQUIRED"))?
        .to_ascii_lowercase();
    let bootstrap = input(&dir, BOOTSTRAP_FILE)?;
    if digest(&bootstrap.raw) != hash {
        return Err(RacpError::new("SERVICE_BOOTSTRAP_CHANGED"));
    }
    reject_duplicate_keys(&bootstrap.raw)?;
    let enrollment: crate::native_client::Enrollment = serde_json::from_slice(&bootstrap.raw)?;
    let supplied = racp_core::validate_local_path(&enrollment.connection_file)?;
    let expected = racp_core::validate_local_path(&root.join(CONNECTION_FILE))?;
    if !racp_core::path_within(&supplied, &expected)
        || !racp_core::path_within(&expected, &supplied)
    {
        return Err(RacpError::new("SERVICE_BOOTSTRAP_INVALID"));
    }
    let connection = input(&dir, CONNECTION_FILE)?;
    let connection_hash = digest(&connection.raw);
    if connection_hash != enrollment.connection_sha256.to_ascii_lowercase() {
        return Err(RacpError::new("CONNECTION_FILE_CHANGED"));
    }
    // Completion wins simultaneous cancellation; otherwise the durable reservation
    // remains unknown and blocks consuming the one-use token again.
    if cancel.is_cancelled() {
        return Err(RacpError::new("CANCELLED"));
    }
    tokio::select! { biased;
        result = crate::native_client::enroll_service(&bootstrap.raw, root) => { result?; },
        _ = cancel.cancelled() => return Err(RacpError::new("REGISTRATION_OUTCOME_UNKNOWN")),
        _ = tokio::time::sleep(std::time::Duration::from_secs(120)) => return Err(RacpError::new("REGISTRATION_OUTCOME_UNKNOWN")),
    }
    let cleanup = Cleanup {
        format_version: 1,
        device_id: NativeState::load(root)?.device_id,
        bootstrap_sha256: hash,
        bootstrap_revision: bootstrap.revision.clone(),
        connection_sha256: connection_hash,
        connection_revision: connection.revision.clone(),
    };
    racp_core::atomic_write(&root.join(CLEANUP), &serde_json::to_vec(&cleanup)?, false)?;
    // Release read pins only after the durable cleanup record; deletion reopens
    // with DELETE access and compares identity+hash while denying concurrent writers.
    drop(bootstrap);
    drop(connection);
    finish(root, &dir)
}
