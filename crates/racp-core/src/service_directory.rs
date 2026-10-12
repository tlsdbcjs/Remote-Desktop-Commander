//! Readable control metadata is separated from the service's private credential/state.
use crate::{validate_local_path, Directory, Workspaces};
use racp_contract::{service_scope::ServiceScope, RacpError};
use std::{collections::BTreeMap, ffi::c_void, os::windows::io::AsRawHandle, path::Path};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
};

struct Local(*mut c_void);
impl Drop for Local {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
pub fn expected_service_sid() -> Result<String, RacpError> {
    let mut sid = [0u8; SECURITY_MAX_SID_SIZE as usize];
    let mut size = sid.len() as u32;
    let mut domain = [0u16; 256];
    let mut domain_size = domain.len() as u32;
    let mut kind = 0;
    if unsafe {
        LookupAccountNameW(
            std::ptr::null(),
            wide(racp_contract::service_scope::SERVICE_ACCOUNT).as_ptr(),
            sid.as_mut_ptr().cast(),
            &mut size,
            domain.as_mut_ptr(),
            &mut domain_size,
            &mut kind,
        )
    } == 0
    {
        return Err(RacpError::new("SERVICE_IDENTITY_UNAVAILABLE"));
    }
    let mut text = std::ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(sid.as_mut_ptr().cast(), &mut text) } == 0 {
        return Err(RacpError::new("SERVICE_IDENTITY_UNAVAILABLE"));
    }
    let _text = Local(text.cast());
    let mut length = 0;
    unsafe {
        while length <= 128 && *text.add(length) != 0 {
            length += 1;
        }
    }
    if length > 128 {
        return Err(RacpError::new("SERVICE_IDENTITY_UNAVAILABLE"));
    }
    let sid = String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
        .map_err(|_| RacpError::new("SERVICE_IDENTITY_UNAVAILABLE"))?;
    if !racp_contract::service_scope::service_sid(&sid) {
        return Err(RacpError::new("SERVICE_IDENTITY_UNAVAILABLE"));
    }
    Ok(sid)
}
pub fn require_service_actor() -> Result<(), RacpError> {
    if crate::native_directory::current_sid()? != expected_service_sid()? {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}
fn entries(scope: &ServiceScope) -> Result<BTreeMap<String, u32>, RacpError> {
    scope.validate()?;
    if scope.service_sid != expected_service_sid()? {
        return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
    }
    let mut entries = BTreeMap::from([
        (scope.service_sid.clone(), FILE_ALL_ACCESS),
        ("S-1-5-18".into(), FILE_ALL_ACCESS),
    ]);
    for entry in &scope.controllers {
        entries.insert(
            entry.user_sid.clone(),
            FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
        );
    }
    Ok(entries)
}
pub fn create(root: &Path, scope: &ServiceScope) -> Result<Directory, RacpError> {
    validate_local_path(root)?;
    let entries = entries(scope)?;
    if !root.try_exists()? {
        let parent = root
            .parent()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        let guards = Workspaces::new(parent, &[])?;
        let _parent = guards.directory("default", parent)?;
        let mut sddl = format!("O:{}D:P", scope.service_sid);
        for (sid, rights) in entries {
            sddl.push_str(&format!("(A;OICI;0x{rights:08x};;;{sid})"));
        }
        let mut security = std::ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                wide(&sddl).as_ptr(),
                SDDL_REVISION_1,
                &mut security,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
        let _security = Local(security);
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: security,
            bInheritHandle: 0,
        };
        if unsafe { CreateDirectoryW(wide(&root.to_string_lossy()).as_ptr(), &attributes) } == 0 {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
    }
    pin(root, scope)
}
pub fn pin(root: &Path, scope: &ServiceScope) -> Result<Directory, RacpError> {
    pin_container(root, scope, true)
}
fn pin_container(
    root: &Path,
    scope: &ServiceScope,
    children: bool,
) -> Result<Directory, RacpError> {
    let directory = pin_acl(root, &scope.service_sid, entries(scope)?)?;
    if children {
        verify_shared_files(&directory, scope, None)?;
    }
    Ok(directory)
}

pub const PAIR_USER_ACCESS: u32 =
    FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE | DELETE;
pub fn pin_pair(root: &Path, service_sid: &str, user_sid: &str) -> Result<Directory, RacpError> {
    if service_sid != expected_service_sid()?
        || !racp_contract::service_scope::interactive_user_sid(user_sid)
    {
        return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
    }
    pin_acl(
        root,
        service_sid,
        BTreeMap::from([
            (service_sid.to_owned(), FILE_ALL_ACCESS),
            ("S-1-5-18".into(), FILE_ALL_ACCESS),
            (user_sid.to_owned(), PAIR_USER_ACCESS),
        ]),
    )
}
fn pin_acl(
    root: &Path,
    expected_owner: &str,
    mut expected: BTreeMap<String, u32>,
) -> Result<Directory, RacpError> {
    let guards = Workspaces::new(root, &[])?;
    let directory = guards.directory("default", root)?;
    let mut owner = std::ptr::null_mut();
    let mut dacl = std::ptr::null_mut();
    let mut security = std::ptr::null_mut();
    if unsafe {
        GetSecurityInfo(
            directory.handle().as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut security,
        )
    } != ERROR_SUCCESS
        || security.is_null()
    {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    let _security = Local(security);
    fn text(sid: PSID) -> Result<String, RacpError> {
        let mut raw = std::ptr::null_mut();
        if sid.is_null() || unsafe { ConvertSidToStringSidW(sid, &mut raw) } == 0 {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
        let _raw = Local(raw.cast());
        let mut length = 0;
        unsafe {
            while length <= 128 && *raw.add(length) != 0 {
                length += 1;
            }
        }
        if length > 128 {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
        String::from_utf16(unsafe { std::slice::from_raw_parts(raw, length) })
            .map_err(|_| RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"))
    }
    let mut flags = 0;
    let mut revision = 0;
    if text(owner)? != expected_owner
        || dacl.is_null()
        || unsafe { GetSecurityDescriptorControl(security, &mut flags, &mut revision) } == 0
        || flags & SE_DACL_PROTECTED == 0
        || unsafe { (*dacl).AceCount } as usize != expected.len()
    {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    let count = expected.len();
    for index in 0..count {
        let mut raw = std::ptr::null_mut();
        if unsafe { GetAce(dacl, index as u32, &mut raw) } == 0 || raw.is_null() {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
        let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
        let sid = (&ace.SidStart as *const u32).cast_mut().cast();
        if ace.Header.AceType != 0
            || ace.Header.AceFlags != (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8
            || expected.remove(&text(sid)?) != Some(ace.Mask)
        {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
    }
    if !expected.is_empty() {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    Ok(directory)
}
fn sid_text(sid: PSID) -> Result<String, RacpError> {
    let mut raw = std::ptr::null_mut();
    if sid.is_null() || unsafe { ConvertSidToStringSidW(sid, &mut raw) } == 0 {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    let _raw = Local(raw.cast());
    let mut length = 0;
    unsafe {
        while length <= 128 && *raw.add(length) != 0 {
            length += 1;
        }
    }
    if length > 128 {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    String::from_utf16(unsafe { std::slice::from_raw_parts(raw, length) })
        .map_err(|_| RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"))
}
fn verify_shared_files(
    directory: &Directory,
    scope: &ServiceScope,
    alternate: Option<&ServiceScope>,
) -> Result<(), RacpError> {
    let names = directory.names()?;
    if names.len() > 3
        || names.iter().any(|name| {
            ![
                "native-endpoint.json",
                "native-ipc.lock",
                "desktop-sessions",
            ]
            .contains(&name.as_str())
        })
    {
        return Err(RacpError::new("SERVICE_CONTROL_CONTENT_CONFLICT"));
    }
    for name in names {
        if name == "desktop-sessions" {
            let root = directory.path.join(&name);
            pin_container(&root, scope, false).or_else(|error| {
                alternate
                    .map(|next| pin_container(&root, next, false))
                    .unwrap_or(Err(error))
            })?;
            continue;
        }
        let expected = entries(scope)?;
        let alternate = alternate.map(entries).transpose()?;
        let mut actual = BTreeMap::new();
        // IPC lock is held with write access by the live Agent; permit its existing
        // writer while reading only security metadata. The directory remains pinned.
        let file = directory.open_read(&name)?;
        if file.metadata()?.len() > 16384 {
            return Err(RacpError::new("SERVICE_CONTROL_CONTENT_CONFLICT"));
        }
        let mut owner = std::ptr::null_mut();
        let mut acl = std::ptr::null_mut();
        let mut security = std::ptr::null_mut();
        if unsafe {
            GetSecurityInfo(
                file.as_raw_handle(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                std::ptr::null_mut(),
                &mut acl,
                std::ptr::null_mut(),
                &mut security,
            )
        } != ERROR_SUCCESS
            || security.is_null()
        {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
        let _security = Local(security);
        let mut flags = 0;
        let mut revision = 0;
        if sid_text(owner)? != scope.service_sid
            || acl.is_null()
            || unsafe { GetSecurityDescriptorControl(security, &mut flags, &mut revision) } == 0
            || flags & SE_DACL_PROTECTED != 0
            || unsafe { (*acl).AceCount } > 18
        {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
        for index in 0..unsafe { (*acl).AceCount } as u32 {
            let mut raw = std::ptr::null_mut();
            if unsafe { GetAce(acl, index, &mut raw) } == 0 || raw.is_null() {
                return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
            }
            let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
            let sid = (&ace.SidStart as *const u32).cast_mut().cast();
            if ace.Header.AceType != 0
                || ace.Header.AceFlags & INHERITED_ACE as u8 == 0
                || ace.Header.AceFlags & INHERIT_ONLY_ACE as u8 != 0
                || actual.insert(sid_text(sid)?, ace.Mask).is_some()
            {
                return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
            }
        }
        if actual != expected && alternate.as_ref().is_none_or(|value| *value != actual) {
            return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
        }
    }
    Ok(())
}
/// Explicit stopped-service scope update. Refuse an ACL that is neither the
/// admitted prior value nor already the desired value; do not repair broad ACLs.
pub fn change_scope(
    root: &Path,
    old: &ServiceScope,
    next: &ServiceScope,
) -> Result<Directory, RacpError> {
    require_service_actor()?;
    if old.service_sid != next.service_sid {
        return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
    }
    let desired = entries(next)?;
    let before = pin_container(root, old, false).or_else(|_| pin_container(root, next, false))?;
    verify_shared_files(&before, old, Some(next))?;
    let names = before.names()?;
    if names.len() > 3
        || names.iter().any(|name| {
            ![
                "native-endpoint.json",
                "native-ipc.lock",
                "desktop-sessions",
            ]
            .contains(&name.as_str())
        })
    {
        return Err(RacpError::new("SERVICE_CONTROL_CONTENT_CONFLICT"));
    }
    for name in &names {
        if name == "desktop-sessions" {
            let sessions = pin_container(&root.join(name), old, false)
                .or_else(|_| pin_container(&root.join(name), next, false))?;
            if !sessions.names()?.is_empty() {
                return Err(RacpError::new("SERVICE_SESSION_CLEANUP_REQUIRED"));
            }
            // Empty child uses this same exact ACL update, with no mutable pair files.
            change_scope(&root.join(name), old, next)?;
            continue;
        }
        let info = before.require_file(name)?;
        if info.link || info.directory || info.size > 16384 {
            return Err(RacpError::new("SERVICE_CONTROL_CONTENT_CONFLICT"));
        }
    }
    use std::os::windows::fs::OpenOptionsExt;
    let writable = std::fs::OpenOptions::new()
        .access_mode(READ_CONTROL | WRITE_DAC)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(root)?;
    let opened = crate::FileInfo::from_file(&writable)?;
    let expected = before.own_info()?;
    if opened.link
        || !opened.directory
        || (opened.device, opened.inode) != (expected.device, expected.inode)
    {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    let mut sddl = format!("O:{}D:P", next.service_sid);
    for (sid, rights) in desired {
        sddl.push_str(&format!("(A;OICI;0x{rights:08x};;;{sid})"));
    }
    let mut security = std::ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            wide(&sddl).as_ptr(),
            SDDL_REVISION_1,
            &mut security,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    let _security = Local(security);
    let mut present = 0;
    let mut defaulted = 0;
    let mut acl = std::ptr::null_mut();
    if unsafe { GetSecurityDescriptorDacl(security, &mut present, &mut acl, &mut defaulted) } == 0
        || present == 0
        || acl.is_null()
    {
        return Err(RacpError::new("SERVICE_CONTROL_NOT_PRIVATE"));
    }
    if unsafe {
        SetSecurityInfo(
            writable.as_raw_handle(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            acl,
            std::ptr::null_mut(),
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("SERVICE_CONTROL_UPDATE_UNKNOWN"));
    }
    pin(root, next)
}

/// Service-owned read-only container; writable pairing buckets have separate protected ACLs.
pub fn sessions(root: &Path, scope: &ServiceScope) -> Result<Directory, RacpError> {
    require_service_actor()?;
    let _control = pin(root, scope)?;
    create(&root.join("desktop-sessions"), scope).or_else(|error| {
        // This directory contains pairing buckets rather than endpoint metadata.
        if root.join("desktop-sessions").try_exists()? {
            pin_container(&root.join("desktop-sessions"), scope, false)
        } else {
            Err(error)
        }
    })
}
