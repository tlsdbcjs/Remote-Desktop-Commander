//! Hash verification and native executable pins retained through GUI/launcher lifetime.
use crate::{runtime_admission::validate_host_binary, Directory, Workspaces};
use racp_contract::{distribution::*, local_ipc::reject_duplicate_keys, RacpError};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::Path,
};
pub struct PinnedPayload {
    pub manifest: NativePayload,
    _files: Vec<File>,
    _manifest: File,
    _root: Directory,
}
fn reject_hard_link(file: &File) -> Result<(), RacpError> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
        };
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.nNumberOfLinks != 1
        {
            return Err(RacpError::new("PACKAGE_INVALID"));
        }
    }
    #[cfg(not(windows))]
    let _ = file;
    Ok(())
}
pub fn hash_file(file: &mut File, max_bytes: u64) -> Result<(u64, String), RacpError> {
    file.seek(SeekFrom::Start(0))?;
    let mut hash = Sha256::new();
    let mut size = 0u64;
    let mut buffer = [0u8; 65536];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        size = size
            .checked_add(n as u64)
            .filter(|v| *v <= max_bytes)
            .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
        hash.update(&buffer[..n]);
    }
    Ok((size, format!("{:x}", hash.finalize())))
}
pub fn pin_payload(root: &Path) -> Result<PinnedPayload, RacpError> {
    pin_payload_kind(root, Some(PayloadKind::Client))
}
/// Service host, provisioning and maintenance require an explicit GUI-free role.
pub fn pin_agent_service_payload(root: &Path) -> Result<PinnedPayload, RacpError> {
    pin_payload_kind(root, Some(PayloadKind::AgentService))
}
/// Shared Agent workers may run in either verified distribution; GUI callers may not.
pub fn pin_agent_payload(root: &Path) -> Result<PinnedPayload, RacpError> {
    pin_payload_kind(root, None)
}
fn pin_payload_kind(
    root: &Path,
    expected: Option<PayloadKind>,
) -> Result<PinnedPayload, RacpError> {
    if !cfg!(all(windows, target_arch = "x86_64")) {
        return Err(RacpError::new("PACKAGE_PLATFORM_MISMATCH"));
    }
    let guard = Workspaces::new(root, &[])?;
    let directory = guard.directory("default", root)?;
    let mut manifest_file = directory.open_pinned_read(MANIFEST_FILE)?;
    reject_hard_link(&manifest_file)?;
    let mut bytes = Vec::new();
    (&mut manifest_file)
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 1024 * 1024 {
        return Err(RacpError::new("PACKAGE_INVALID"));
    }
    reject_duplicate_keys(&bytes)?;
    let manifest: NativePayload = serde_json::from_slice(&bytes)?;
    manifest.validate()?;
    if expected.is_some_and(|kind| kind != manifest.kind) {
        return Err(RacpError::new("PACKAGE_KIND_MISMATCH"));
    }
    let allowed_files = manifest.kind.files();
    let names = directory.names()?;
    if names.len() != allowed_files.len() + 1
        || names
            .iter()
            .any(|name| name != MANIFEST_FILE && !allowed_files.contains(&name.as_str()))
    {
        return Err(RacpError::new("PACKAGE_INVALID"));
    }
    let mut files = Vec::new();
    for entry in &manifest.files {
        let mut file = directory.open_pinned_read(&entry.path)?;
        reject_hard_link(&file)?;
        let (size, digest) = hash_file(&mut file, MAX_PAYLOAD_BYTES / 2)?;
        if size != entry.size_bytes || digest != entry.sha256 {
            return Err(RacpError::new("PACKAGE_HASH_MISMATCH"));
        }
        if entry.path.ends_with(".exe") {
            file.seek(SeekFrom::Start(0))?;
            validate_host_binary(&mut file)?;
        }
        files.push(file);
    }
    Ok(PinnedPayload {
        manifest,
        _files: files,
        _manifest: manifest_file,
        _root: directory,
    })
}
/// Development binaries have no sibling manifest; packaged binaries must verify it.
pub fn pin_current_payload(required: bool) -> Result<Option<PinnedPayload>, RacpError> {
    pin_current_kind(required, Some(PayloadKind::Client))
}
pub fn pin_current_agent_payload(required: bool) -> Result<Option<PinnedPayload>, RacpError> {
    pin_current_kind(required, None)
}
pub fn pin_current_agent_service_payload() -> Result<Option<PinnedPayload>, RacpError> {
    pin_current_kind(true, Some(PayloadKind::AgentService))
}
fn pin_current_kind(
    required: bool,
    expected: Option<PayloadKind>,
) -> Result<Option<PinnedPayload>, RacpError> {
    let exe = std::env::current_exe()?;
    let root = exe
        .parent()
        .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
    crate::validate_local_path(root)?;
    if root.join(MANIFEST_FILE).try_exists()? {
        Ok(Some(pin_payload_kind(root, expected)?))
    } else if required {
        Err(RacpError::new("PACKAGE_INVALID"))
    } else {
        Ok(None)
    }
}
