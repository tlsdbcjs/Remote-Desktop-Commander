//! Versioned machine-service installation metadata. No service start or state import.
use racp_contract::{digest, local_ipc::reject_duplicate_keys, RacpError, VERSION};
use racp_core::{machine_service as machine, Workspaces};
use serde_json::{json, Value};
use std::{os::windows::io::AsRawHandle, path::Path};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::Services::*,
};
const RECEIPT: &str = "machine-install.json";
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
struct Local(*mut core::ffi::c_void);
impl Drop for Local {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}
struct Service(SC_HANDLE);
impl Drop for Service {
    fn drop(&mut self) {
        unsafe {
            CloseServiceHandle(self.0);
        }
    }
}
fn root() -> Result<std::path::PathBuf, RacpError> {
    let root = machine::install_parent()?.join(VERSION);
    let exe = racp_core::validate_local_path(&std::env::current_exe()?)?;
    let expected = root.join("payload/racp-agent.exe");
    if !racp_core::path_within(&exe, &expected) || !racp_core::path_within(&expected, &exe) {
        return Err(RacpError::new("MACHINE_INSTALL_PATH_INVALID"));
    }
    Ok(root)
}
fn protect(path: &Path, directory: bool) -> Result<(), RacpError> {
    use std::os::windows::fs::OpenOptionsExt;
    let file = std::fs::OpenOptions::new()
        .access_mode(READ_CONTROL | WRITE_DAC | WRITE_OWNER)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(
            FILE_FLAG_OPEN_REPARSE_POINT
                | if directory {
                    FILE_FLAG_BACKUP_SEMANTICS
                } else {
                    0
                },
        )
        .open(path)?;
    let info = racp_core::FileInfo::from_file(&file)?;
    if info.link || info.directory != directory {
        return Err(RacpError::new("MACHINE_INSTALL_CONTENT_CHANGED"));
    }
    let mut descriptor = std::ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            wide("O:BAD:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;FRFX;;;AU)").as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("MACHINE_INSTALL_ACCESS_DENIED"));
    }
    let _descriptor = Local(descriptor);
    let mut owner = std::ptr::null_mut();
    let mut defaulted = 0;
    let mut present = 0;
    let mut acl = std::ptr::null_mut();
    if unsafe { GetSecurityDescriptorOwner(descriptor, &mut owner, &mut defaulted) } == 0
        || unsafe { GetSecurityDescriptorDacl(descriptor, &mut present, &mut acl, &mut defaulted) }
            == 0
        || present == 0
        || acl.is_null()
    {
        return Err(RacpError::new("MACHINE_INSTALL_ACCESS_DENIED"));
    }
    if unsafe {
        SetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION
                | DACL_SECURITY_INFORMATION
                | PROTECTED_DACL_SECURITY_INFORMATION,
            owner,
            std::ptr::null_mut(),
            acl,
            std::ptr::null_mut(),
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("MACHINE_INSTALL_ACCESS_DENIED"));
    }
    Ok(())
}
pub fn prepare() -> Result<Value, RacpError> {
    super::machine_setup::administrator()?;
    let root = root()?;
    let parent = machine::install_parent()?;
    let _program_files = machine::pin_secure_parent(
        parent
            .parent()
            .ok_or_else(|| RacpError::new("MACHINE_INSTALL_PATH_INVALID"))?,
    )?;
    let payload = root.join("payload");
    let _payload = racp_core::distribution::pin_payload(&payload)?;
    let guards = Workspaces::new(&root, &[])?;
    let version = guards.directory("default", &root)?;
    if version
        .names()?
        .iter()
        .any(|name| !["payload", "uninstall.exe"].contains(&name.as_str()))
        || version.names()?.len() != 2
    {
        return Err(RacpError::new("MACHINE_INSTALL_CONFLICT"));
    }
    let _uninstaller = version.open_pinned_read("uninstall.exe")?;
    let mut uninstaller = version.open_pinned_read("uninstall.exe")?;
    let (_, uninstaller_hash) =
        racp_core::distribution::hash_file(&mut uninstaller, 64 * 1024 * 1024)?;
    // An existing trusted shared container is retained. Only a new, single-version
    // container can be assigned its initial protected ownership.
    if machine::pin_secure_parent(&parent).is_err() {
        let shared = Workspaces::new(&parent, &[])?;
        let shared = shared.directory("default", &parent)?;
        if shared.names()? != [VERSION] {
            return Err(RacpError::new("MACHINE_INSTALL_CONFLICT"));
        }
        protect(&parent, true)?;
    }
    let _shared = machine::pin_secure_parent(&parent)?;
    protect(&root, true)?;
    protect(&payload, true)?;
    for name in racp_core::Workspaces::new(&payload, &[])?
        .directory("default", &payload)?
        .names()?
    {
        protect(&payload.join(name), false)?;
    }
    protect(&root.join("uninstall.exe"), false)?;
    let _image = machine::pin_image_directory(&payload)?;
    let value = json!({"format_version":1,"version":VERSION,"scope":"machine_service","uninstaller_sha256":uninstaller_hash,
        "payload_manifest_sha256":digest(racp_core::read_bounded(&payload.join(racp_contract::distribution::MANIFEST_FILE),1048576,false)?),
        "service_started":false,"registered":false});
    racp_core::atomic_write(&root.join(RECEIPT), &serde_json::to_vec(&value)?, false)?;
    protect(&root.join(RECEIPT), false)?;
    machine::pin_secure_parent(&root)?;
    Ok(value)
}
pub fn uninstall_preflight() -> Result<Value, RacpError> {
    super::machine_setup::administrator()?;
    let root = root()?;
    let _root = machine::pin_secure_parent(&root)?;
    let payload = root.join("payload");
    let _payload = racp_core::distribution::pin_payload(&payload)?;
    let _image = machine::pin_image_directory(&payload)?;
    let raw = racp_core::read_bounded(&root.join(RECEIPT), 4096, false)?;
    reject_duplicate_keys(&raw)?;
    let value: Value = serde_json::from_slice(&raw)?;
    if value["format_version"] != 1
        || value["scope"] != "machine_service"
        || value["version"] != VERSION
        || value["payload_manifest_sha256"]
            != digest(racp_core::read_bounded(
                &payload.join(racp_contract::distribution::MANIFEST_FILE),
                1048576,
                false,
            )?)
    {
        return Err(RacpError::new("MACHINE_INSTALL_RECEIPT_CHANGED"));
    }
    let guards = Workspaces::new(&root, &[])?;
    let directory = guards.directory("default", &root)?;
    if directory.names()?.len() != 3
        || directory
            .names()?
            .iter()
            .any(|name| !["payload", "uninstall.exe", RECEIPT].contains(&name.as_str()))
    {
        return Err(RacpError::new("MACHINE_INSTALL_CONTENT_CHANGED"));
    }
    let mut uninstaller = directory.open_pinned_read("uninstall.exe")?;
    if value["uninstaller_sha256"]
        != racp_core::distribution::hash_file(&mut uninstaller, 64 * 1024 * 1024)?.1
    {
        return Err(RacpError::new("MACHINE_INSTALL_RECEIPT_CHANGED"));
    }
    let manager = unsafe { OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_CONNECT) };
    if manager.is_null() {
        return Err(RacpError::new("MACHINE_INSTALL_ACCESS_DENIED"));
    }
    let manager = Service(manager);
    for name in [
        racp_contract::service_scope::SERVICE_NAME,
        "RACPNativeSetup",
    ] {
        let service = unsafe { OpenServiceW(manager.0, wide(name).as_ptr(), SERVICE_QUERY_CONFIG) };
        if !service.is_null() {
            drop(Service(service));
            return Err(RacpError::new("MACHINE_INSTALL_SERVICE_STILL_CONFIGURED"));
        }
        if unsafe { GetLastError() } != ERROR_SERVICE_DOES_NOT_EXIST {
            return Err(RacpError::new("MACHINE_INSTALL_SERVICE_STATUS_UNKNOWN"));
        }
    }
    Ok(
        json!({"preflight_complete":true,"scope":"machine_service","state_preserved":true,
        "service_configured":false,"gui_must_be_closed":true,"payload_manifest_sha256":value["payload_manifest_sha256"]}),
    )
}
