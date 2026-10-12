//! Local SYSTEM installer/handoff boundaries, separate from the virtual service actor.
use crate::{Directory, Workspaces};
use racp_contract::RacpError;
use std::os::windows::io::{FromRawHandle, OwnedHandle};
use std::{ffi::c_void, os::windows::io::AsRawHandle, path::Path};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::{
        Com::CoTaskMemFree,
        Threading::{GetCurrentProcess, OpenProcessToken},
    },
    UI::Shell::{FOLDERID_ProgramData, FOLDERID_ProgramFilesX64, SHGetKnownFolderPath},
};
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
pub fn data_parent() -> Result<std::path::PathBuf, RacpError> {
    Ok(known_folder(&FOLDERID_ProgramData)?.join("RACP-Native-Service"))
}
pub fn install_parent() -> Result<std::path::PathBuf, RacpError> {
    Ok(known_folder(&FOLDERID_ProgramFilesX64)?.join("RACP-Native"))
}
/// Machine service packages do not create per-user Agent autorun references.
pub fn is_service_install(exe: &Path) -> Result<bool, RacpError> {
    let root = install_parent()?.join(racp_contract::VERSION);
    if !crate::path_within(exe, &root.join("payload")) {
        return Ok(false);
    }
    let _root = pin_secure_parent(&root)?;
    let raw = crate::read_bounded(&root.join("machine-install.json"), 4096, false)?;
    racp_contract::local_ipc::reject_duplicate_keys(&raw)?;
    let value: serde_json::Value = serde_json::from_slice(&raw)?;
    if value["format_version"] != 1
        || value["scope"] != "machine_service"
        || value["version"] != racp_contract::VERSION
        || value["payload_manifest_sha256"]
            != racp_contract::digest(crate::read_bounded(
                &root
                    .join("payload")
                    .join(racp_contract::distribution::MANIFEST_FILE),
                1048576,
                false,
            )?)
    {
        return Err(RacpError::new("MACHINE_INSTALL_RECEIPT_CHANGED"));
    }
    Ok(true)
}
fn known_folder(folder: &windows_sys::core::GUID) -> Result<std::path::PathBuf, RacpError> {
    let mut raw = std::ptr::null_mut();
    if unsafe { SHGetKnownFolderPath(folder, 0, std::ptr::null_mut(), &mut raw) } < 0
        || raw.is_null()
    {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    struct Folder(*mut u16);
    impl Drop for Folder {
        fn drop(&mut self) {
            unsafe {
                CoTaskMemFree(self.0.cast());
            }
        }
    }
    let _folder = Folder(raw);
    let mut len = 0;
    while len <= 32768 && unsafe { *raw.add(len) } != 0 {
        len += 1;
    }
    if len > 32768 {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let text = String::from_utf16(unsafe { std::slice::from_raw_parts(raw, len) })
        .map_err(|_| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))?;
    crate::validate_local_path(&std::path::PathBuf::from(text))
}
/// Explicit fresh ownership assignment only. Restore the caller's original privilege state.
pub struct RestorePrivilege {
    token: OwnedHandle,
    previous: TOKEN_PRIVILEGES,
}
impl RestorePrivilege {
    pub fn enable() -> Result<Self, RacpError> {
        require_system()?;
        let mut token = std::ptr::null_mut();
        if unsafe {
            OpenProcessToken(
                GetCurrentProcess(),
                TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES,
                &mut token,
            )
        } == 0
        {
            return Err(RacpError::new("SERVICE_PROVISION_PRIVILEGE_UNAVAILABLE"));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token) };
        let mut luid = LUID::default();
        if unsafe {
            LookupPrivilegeValueW(
                std::ptr::null(),
                wide("SeRestorePrivilege").as_ptr(),
                &mut luid,
            )
        } == 0
        {
            return Err(RacpError::new("SERVICE_PROVISION_PRIVILEGE_UNAVAILABLE"));
        }
        let next = TOKEN_PRIVILEGES {
            PrivilegeCount: 1,
            Privileges: [LUID_AND_ATTRIBUTES {
                Luid: luid,
                Attributes: SE_PRIVILEGE_ENABLED,
            }],
        };
        let mut previous = TOKEN_PRIVILEGES::default();
        let mut size = 0;
        if unsafe {
            AdjustTokenPrivileges(
                token.as_raw_handle(),
                0,
                &next,
                std::mem::size_of_val(&previous) as u32,
                &mut previous,
                &mut size,
            )
        } == 0
            || unsafe { GetLastError() } != ERROR_SUCCESS
        {
            return Err(RacpError::new("SERVICE_PROVISION_PRIVILEGE_UNAVAILABLE"));
        }
        Ok(Self { token, previous })
    }
}
impl Drop for RestorePrivilege {
    fn drop(&mut self) {
        if unsafe {
            AdjustTokenPrivileges(
                self.token.as_raw_handle(),
                0,
                &self.previous,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        } == 0
        {
            std::process::abort();
        }
    }
}
fn fresh(root: &Path, descriptor: &str) -> Result<(), RacpError> {
    require_system()?;
    crate::validate_local_path(root)?;
    if root.try_exists()? {
        return Err(RacpError::new("SERVICE_PROVISION_CONFLICT"));
    }
    let parent = root
        .parent()
        .ok_or_else(|| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))?;
    let guard = pin_secure_parent(parent)?;
    let mut security = std::ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            wide(descriptor).as_ptr(),
            SDDL_REVISION_1,
            &mut security,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let _security = Local(security);
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: security,
        bInheritHandle: 0,
    };
    if unsafe { CreateDirectoryW(wide(&root.to_string_lossy()).as_ptr(), &attributes) } == 0 {
        return Err(RacpError::new("SERVICE_PROVISION_CONFLICT"));
    }
    drop(guard);
    Ok(())
}
pub fn create_system_parent(root: &Path) -> Result<Directory, RacpError> {
    fresh(root, "O:SYD:P(A;OICI;FA;;;SY)")?;
    pin_secure_parent(root)
}
pub fn create_private(root: &Path) -> Result<Directory, RacpError> {
    let sid = crate::service_directory::expected_service_sid()?;
    fresh(
        root,
        &format!("O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)"),
    )?;
    pin_private(root)
}
pub fn grant_parent_read(
    root: &Path,
    scope: &racp_contract::service_scope::ServiceScope,
) -> Result<(), RacpError> {
    require_system()?;
    scope.validate()?;
    if scope.service_sid != crate::service_directory::expected_service_sid()? {
        return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
    }
    let pin = pin_secure_parent(root)?;
    let (_descriptor, owner, _, _) = security(pin.handle())?;
    if owner != "S-1-5-18" {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let mut sddl = format!(
        "O:SYD:P(A;OICI;FA;;;SY)(A;OICI;FRFX;;;{})",
        scope.service_sid
    );
    for user in &scope.controllers {
        sddl.push_str(&format!("(A;OICI;FRFX;;;{})", user.user_sid));
    }
    let mut descriptor = std::ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            wide(&sddl).as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let _descriptor = Local(descriptor);
    if unsafe {
        SetFileSecurityW(
            wide(&root.to_string_lossy()).as_ptr(),
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            descriptor,
        )
    } == 0
    {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    drop(pin);
    let pin = pin_secure_parent(root)?;
    let (_descriptor, owner, acl, flags) = security(pin.handle())?;
    let mut expected = std::collections::BTreeMap::from([
        ("S-1-5-18".to_owned(), FILE_ALL_ACCESS),
        (
            scope.service_sid.clone(),
            FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
        ),
    ]);
    for user in &scope.controllers {
        expected.insert(
            user.user_sid.clone(),
            FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
        );
    }
    if owner != "S-1-5-18"
        || flags & SE_DACL_PROTECTED == 0
        || unsafe { (*acl).AceCount } as usize != expected.len()
    {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    for index in 0..unsafe { (*acl).AceCount } as u32 {
        let mut raw = std::ptr::null_mut();
        if unsafe { GetAce(acl, index, &mut raw) } == 0 || raw.is_null() {
            return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
        }
        let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
        if ace.Header.AceType != 0
            || ace.Header.AceFlags != (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8
            || expected.remove(&sid_text((&ace.SidStart as *const u32).cast_mut().cast())?)
                != Some(ace.Mask)
        {
            return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
        }
    }
    if !expected.is_empty() {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    Ok(())
}
struct Local(*mut c_void);
impl Drop for Local {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}
pub fn require_system() -> Result<(), RacpError> {
    if crate::native_directory::current_sid()? != "S-1-5-18" {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}
fn sid_text(sid: PSID) -> Result<String, RacpError> {
    let mut raw = std::ptr::null_mut();
    if sid.is_null() || unsafe { ConvertSidToStringSidW(sid, &mut raw) } == 0 {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let _raw = Local(raw.cast());
    let mut len = 0;
    unsafe {
        while len <= 128 && *raw.add(len) != 0 {
            len += 1;
        }
    }
    if len > 128 {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    String::from_utf16(unsafe { std::slice::from_raw_parts(raw, len) })
        .map_err(|_| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))
}
fn security(file: &std::fs::File) -> Result<(Local, String, *mut ACL, u16), RacpError> {
    let mut owner = std::ptr::null_mut();
    let mut acl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    if unsafe {
        GetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            std::ptr::null_mut(),
            &mut acl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    } != ERROR_SUCCESS
        || descriptor.is_null()
        || acl.is_null()
    {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let descriptor = Local(descriptor);
    let owner = sid_text(owner)?;
    let mut flags = 0;
    let mut revision = 0;
    if unsafe { GetSecurityDescriptorControl(descriptor.0, &mut flags, &mut revision) } == 0 {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    Ok((descriptor, owner, acl, flags))
}
pub fn pin_private(root: &Path) -> Result<Directory, RacpError> {
    pin_private_sid(root, &crate::service_directory::expected_service_sid()?)
}
/// Restore admission uses the sealed config SID until SCM recreation permits OS lookup.
pub fn pin_private_sid(root: &Path, expected_sid: &str) -> Result<Directory, RacpError> {
    require_system()?;
    if !racp_contract::service_scope::service_sid(expected_sid) {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let guards = Workspaces::new(root, &[])?;
    let directory = guards.directory("default", root)?;
    let (_descriptor, owner, acl, flags) = security(directory.handle())?;
    let service = expected_sid.to_owned();
    if owner != service || flags & SE_DACL_PROTECTED == 0 || unsafe { (*acl).AceCount } != 2 {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    let mut expected = std::collections::BTreeSet::from([service, "S-1-5-18".into()]);
    for index in 0..2 {
        let mut raw = std::ptr::null_mut();
        if unsafe { GetAce(acl, index, &mut raw) } == 0 || raw.is_null() {
            return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
        }
        let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
        let sid = (&ace.SidStart as *const u32).cast_mut().cast();
        if ace.Header.AceType != 0
            || ace.Mask != FILE_ALL_ACCESS
            || ace.Header.AceFlags != (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8
            || !expected.remove(&sid_text(sid)?)
        {
            return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
        }
    }
    if !expected.is_empty() {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    Ok(directory)
}
/// A privileged service image must not be supplied from a user-owned/writable install.
pub fn pin_image_directory(root: &Path) -> Result<Directory, RacpError> {
    let directory = pin_secure_parent(root)?;
    let trusted = trusted_installer()?;
    for name in directory.names()? {
        check_object(&directory.open_pinned_read(&name)?, &trusted, true)?;
    }
    Ok(directory)
}
fn trusted_installer() -> Result<String, RacpError> {
    let mut sid = [0u8; SECURITY_MAX_SID_SIZE as usize];
    let mut size = sid.len() as u32;
    let mut domain = [0u16; 256];
    let mut domain_size = domain.len() as u32;
    let mut kind = 0;
    if unsafe {
        LookupAccountNameW(
            std::ptr::null(),
            wide("NT SERVICE\\TrustedInstaller").as_ptr(),
            sid.as_mut_ptr().cast(),
            &mut size,
            domain.as_mut_ptr(),
            &mut domain_size,
            &mut kind,
        )
    } == 0
    {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    sid_text(sid.as_mut_ptr().cast())
}
pub fn pin_secure_parent(root: &Path) -> Result<Directory, RacpError> {
    let guards = Workspaces::new(root, &[])?;
    let directory = guards.directory("default", root)?;
    let trusted = trusted_installer()?;
    for ancestor in root.ancestors() {
        let scope = Workspaces::new(ancestor, &[])?;
        let pin = scope.directory("default", ancestor)?;
        check_object(pin.handle(), &trusted, false)?;
    }
    Ok(directory)
}
fn check_object(file: &std::fs::File, trusted: &str, leaf_file: bool) -> Result<(), RacpError> {
    let (_descriptor, owner, acl, _) = security(&file)?;
    let privileged = |sid: &str| ["S-1-5-18", "S-1-5-32-544", trusted].contains(&sid);
    if !privileged(&owner) || unsafe { (*acl).AceCount } > 32 {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    for index in 0..unsafe { (*acl).AceCount } as u32 {
        let mut raw = std::ptr::null_mut();
        if unsafe { GetAce(acl, index, &mut raw) } == 0 || raw.is_null() {
            return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
        }
        let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
        if ace.Header.AceType != 0 {
            return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
        }
        let sid = (&ace.SidStart as *const u32).cast_mut().cast();
        let sid = sid_text(sid)?;
        // A parent's add-subdirectory permission does not replace this protected child.
        // Reject ancestor reparse/DACL/delete mutation and all leaf-file writers.
        if ace.Header.AceFlags & INHERIT_ONLY_ACE as u8 == 0
            && ace.Mask
                & (FILE_WRITE_DATA
                    | if leaf_file {
                        FILE_APPEND_DATA
                    } else {
                        FILE_DELETE_CHILD
                    }
                    | FILE_WRITE_EA
                    | FILE_WRITE_ATTRIBUTES
                    | DELETE
                    | WRITE_DAC
                    | WRITE_OWNER
                    | GENERIC_WRITE
                    | GENERIC_ALL)
                != 0
            && !privileged(&sid)
        {
            return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
        }
    }
    Ok(())
}
