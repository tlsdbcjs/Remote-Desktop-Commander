//! Handle-pinned DACL changes retain a recovery handle and reject actor lockout.
use super::*;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use windows_sys::Win32::{
    Foundation::{LocalFree, ERROR_SUCCESS},
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::Threading::{GetCurrentProcess, OpenProcessToken},
};

struct Local(*mut core::ffi::c_void);
impl Drop for Local {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
}
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
pub(super) struct Descriptor {
    memory: Local,
    pub(super) dacl: *mut ACL,
    pub(super) protected: bool,
    pub(super) sddl: String,
}
fn descriptor(
    handle: windows_sys::Win32::Foundation::HANDLE,
) -> std::result::Result<Descriptor, RacpError> {
    let mut dacl = std::ptr::null_mut();
    let mut sd = std::ptr::null_mut();
    let result = unsafe {
        GetSecurityInfo(
            handle,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut sd,
        )
    };
    if result != ERROR_SUCCESS || sd.is_null() {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let memory = Local(sd);
    let mut text = std::ptr::null_mut();
    let mut control = 0;
    let mut revision = 0;
    if unsafe { GetSecurityDescriptorControl(sd, &mut control, &mut revision) } == 0
        || unsafe {
            ConvertSecurityDescriptorToStringSecurityDescriptorW(
                sd,
                SDDL_REVISION_1,
                OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut text,
                std::ptr::null_mut(),
            )
        } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let _text = Local(text.cast());
    let mut length = 0;
    unsafe {
        while length < 65536 && *text.add(length) != 0 {
            length += 1;
        }
    }
    if length == 65536 {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let sddl = String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
    Ok(Descriptor {
        memory,
        dacl,
        protected: control & SE_DACL_PROTECTED != 0,
        sddl,
    })
}
fn actor_can_recover(sd: *mut core::ffi::c_void) -> std::result::Result<bool, RacpError> {
    let mut token = std::ptr::null_mut();
    if unsafe {
        OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_QUERY | TOKEN_DUPLICATE,
            &mut token,
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let token = unsafe { OwnedHandle::from_raw_handle(token) };
    let mut duplicate = std::ptr::null_mut();
    if unsafe { DuplicateToken(token.as_raw_handle(), SecurityImpersonation, &mut duplicate) } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let duplicate = unsafe { OwnedHandle::from_raw_handle(duplicate) };
    let mut mapping = GENERIC_MAPPING {
        GenericRead: FILE_GENERIC_READ,
        GenericWrite: FILE_GENERIC_WRITE,
        GenericExecute: FILE_GENERIC_EXECUTE,
        GenericAll: FILE_ALL_ACCESS,
    };
    let mut privileges = [0usize; 128];
    let mut length = std::mem::size_of_val(&privileges) as u32;
    let mut granted = 0;
    let mut allowed = 0;
    if unsafe {
        AccessCheck(
            sd,
            duplicate.as_raw_handle(),
            READ_CONTROL | WRITE_DAC,
            &mut mapping,
            privileges.as_mut_ptr().cast(),
            &mut length,
            &mut granted,
            &mut allowed,
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(allowed != 0)
}
fn install(
    handle: windows_sys::Win32::Foundation::HANDLE,
    dacl: *mut ACL,
    protected: bool,
) -> std::result::Result<(), RacpError> {
    let flags = DACL_SECURITY_INFORMATION
        | if protected {
            PROTECTED_DACL_SECURITY_INFORMATION
        } else {
            UNPROTECTED_DACL_SECURITY_INFORMATION
        };
    if unsafe {
        SetSecurityInfo(
            handle,
            SE_FILE_OBJECT,
            flags,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            dacl,
            std::ptr::null_mut(),
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}
pub(super) fn private_directory(directory: &Directory) -> std::result::Result<(), RacpError> {
    let actor = crate::providers::desktop::native_identity::PinnedPeer::open(std::process::id())?;
    let sddl = wide(&format!(
        "D:P(A;OICI;FA;;;{})(A;OICI;FA;;;SY)",
        actor.identity().sid
    ));
    let mut sd = std::ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            SDDL_REVISION_1,
            &mut sd,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let _memory = Local(sd);
    let mut dacl = std::ptr::null_mut();
    let mut present = 0;
    let mut defaulted = 0;
    if unsafe { GetSecurityDescriptorDacl(sd, &mut present, &mut dacl, &mut defaulted) } == 0
        || present == 0
        || dacl.is_null()
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let parent = directory
        .path
        .parent()
        .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?;
    let guards = Workspaces::new(parent, &[])?;
    let parent = guards.directory("default", parent)?;
    let file = parent.open_windows(
        name(&directory.path)?,
        READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES,
    )?;
    install(file.as_raw_handle(), dacl, true)
}
pub(super) fn execute(
    files: &Filesystem,
    workspace: &str,
    path: &Path,
    p: &Value,
    budget: &Budget,
    op: &str,
) -> Result<Value> {
    budget.check()?;
    let parent = files.guards.parent(workspace, path)?;
    let filename = name(path)?;
    let file = parent.open_windows(
        filename,
        READ_CONTROL
            | FILE_READ_ATTRIBUTES
            | if op == "filesystem.acl_modify" {
                WRITE_DAC
            } else {
                0
            },
    )?;
    let metadata = FileInfo::from_file(&file)?;
    let old = descriptor(file.as_raw_handle())?;
    let old_hash = racp_contract::digest(old.sddl.as_bytes());
    if op == "filesystem.acl_read" {
        return Ok(
            json!({"path":path,"revision":metadata.revision(),"acl_sha256":old_hash,"sddl":old.sddl,"inherit_parent":!old.protected,"scope":"windows_dacl"}),
        );
    }
    if p["expected_revision"] != metadata.revision() || p["expected_acl_sha256"] != old_hash {
        return Err(RacpError::new("PRECONDITION_FAILED").into());
    }
    let entries = p["entries"]
        .as_array()
        .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
    let mut sid_memory = vec![];
    let mut access = vec![];
    for entry in entries {
        let sid = wide(
            entry["sid"]
                .as_str()
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?,
        );
        let mut ptr = std::ptr::null_mut();
        if unsafe { ConvertStringSidToSidW(sid.as_ptr(), &mut ptr) } == 0 {
            return Err(RacpError::new("INVALID_ARGUMENT").into());
        }
        sid_memory.push(Local(ptr));
        access.push(EXPLICIT_ACCESS_W {
            grfAccessPermissions: match entry["rights"].as_str() {
                Some("read") => FILE_GENERIC_READ,
                Some("read_write") => FILE_GENERIC_READ | FILE_GENERIC_WRITE,
                Some("full") => FILE_ALL_ACCESS,
                _ => return Err(RacpError::new("INVALID_ARGUMENT").into()),
            },
            grfAccessMode: if entry["access"] == "deny" {
                DENY_ACCESS
            } else {
                SET_ACCESS
            },
            grfInheritance: if entry["inherit"] == true {
                if !metadata.directory {
                    return Err(RacpError::new("INVALID_ARGUMENT").into());
                }
                SUB_CONTAINERS_AND_OBJECTS_INHERIT
            } else {
                NO_INHERITANCE
            },
            Trustee: TRUSTEE_W {
                pMultipleTrustee: std::ptr::null_mut(),
                MultipleTrusteeOperation: NO_MULTIPLE_TRUSTEE,
                TrusteeForm: TRUSTEE_IS_SID,
                TrusteeType: TRUSTEE_IS_UNKNOWN,
                ptstrName: ptr.cast(),
            },
        });
    }
    let mut dacl = std::ptr::null_mut();
    if unsafe {
        SetEntriesInAclW(
            access.len() as u32,
            access.as_ptr(),
            std::ptr::null_mut(),
            &mut dacl,
        )
    } != ERROR_SUCCESS
        || dacl.is_null()
    {
        return Err(RacpError::new("INVALID_ARGUMENT").into());
    }
    let _dacl = Local(dacl.cast());
    budget.check()?;
    // Retain original descriptor and WRITE_DAC handle until readback and rollback are complete.
    install(file.as_raw_handle(), dacl, p["inherit_parent"] != true)?;
    let checked = descriptor(file.as_raw_handle()).and_then(|d| {
        actor_can_recover(d.memory.0).and_then(|allowed| {
            if allowed {
                Ok(d)
            } else {
                Err(RacpError::new("PERMISSION_DENIED"))
            }
        })
    });
    let new = match checked {
        Ok(new) => new,
        Err(error) => {
            let restored = install(file.as_raw_handle(), old.dacl, old.protected)
                .and_then(|_| descriptor(file.as_raw_handle()))
                .is_ok_and(|d| racp_contract::digest(d.sddl.as_bytes()) == old_hash);
            return Err(Failure {
                error: if restored {
                    error
                } else {
                    RacpError::new("CLEANUP_UNKNOWN")
                },
                details: json!({"cleanup_status":if restored{"complete"}else{"partial"},"rolled_back":restored,"before_acl_sha256":old_hash}),
            });
        }
    };
    let revision=FileInfo::from_file(&file).map_err(|_|Failure{error:RacpError::new("EXECUTION_UNKNOWN"),details:json!({"cleanup_status":"partial","side_effect":"committed","before_sddl":old.sddl,"before_acl_sha256":old_hash,"acl_sha256":racp_contract::digest(new.sddl.as_bytes())})})?.revision();
    Ok(
        json!({"path":path,"changed":true,"before_sddl":old.sddl,"before_acl_sha256":old_hash,"acl_sha256":racp_contract::digest(new.sddl.as_bytes()),"sddl":new.sddl,"revision":revision,"inherit_parent":!new.protected,"external_writer_atomic_cas":false}),
    )
}
