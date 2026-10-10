//! Temporary query-only grants on our process/token. Never duplicates another token.
use racp_contract::RacpError;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    System::Threading::*,
};
struct Saved {
    handle: HANDLE,
    descriptor: PSECURITY_DESCRIPTOR,
    acl: *mut ACL,
}
pub struct Grants {
    saved: Vec<Saved>,
    _token: OwnedHandle,
}
unsafe impl Send for Grants {}
impl Drop for Grants {
    fn drop(&mut self) {
        for saved in self.saved.iter().rev() {
            unsafe {
                SetSecurityInfo(
                    saved.handle,
                    SE_KERNEL_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    saved.acl,
                    std::ptr::null_mut(),
                );
                LocalFree(saved.descriptor);
            }
        }
    }
}
pub fn own(principals: &[String], broker: bool) -> Result<Grants, RacpError> {
    let mut token = std::ptr::null_mut();
    if unsafe {
        OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_QUERY | 0x00020000 | 0x00040000,
            &mut token,
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let token = unsafe { OwnedHandle::from_raw_handle(token) };
    let mut result = Grants {
        saved: vec![],
        _token: token,
    };
    for (handle, rights) in [
        (
            unsafe { GetCurrentProcess() },
            if broker { 0x101101 } else { 0x101000 },
        ),
        (result._token.as_raw_handle(), 8),
    ] {
        let mut original = std::ptr::null_mut();
        let mut acl = std::ptr::null_mut();
        if unsafe {
            GetSecurityInfo(
                handle,
                SE_KERNEL_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut acl,
                std::ptr::null_mut(),
                &mut original,
            )
        } != ERROR_SUCCESS
            || acl.is_null()
        {
            if !original.is_null() {
                unsafe {
                    LocalFree(original);
                }
            }
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        result.saved.push(Saved {
            handle,
            descriptor: original,
            acl,
        });
        for principal in principals {
            if !super::pairing::sid(principal) {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            let text: Vec<u16> = principal.encode_utf16().chain(Some(0)).collect();
            let mut sid = std::ptr::null_mut();
            if unsafe { ConvertStringSidToSidW(text.as_ptr(), &mut sid) } == 0 {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            let entry = EXPLICIT_ACCESS_W {
                grfAccessPermissions: rights,
                grfAccessMode: GRANT_ACCESS,
                grfInheritance: NO_INHERITANCE,
                Trustee: TRUSTEE_W {
                    TrusteeForm: TRUSTEE_IS_SID,
                    TrusteeType: TRUSTEE_IS_UNKNOWN,
                    ptstrName: sid.cast(),
                    ..Default::default()
                },
            };
            let mut current = std::ptr::null_mut();
            let mut descriptor = std::ptr::null_mut();
            let status = unsafe {
                GetSecurityInfo(
                    handle,
                    SE_KERNEL_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    &mut current,
                    std::ptr::null_mut(),
                    &mut descriptor,
                )
            };
            let mut updated = std::ptr::null_mut();
            let status = if status == ERROR_SUCCESS {
                unsafe { SetEntriesInAclW(1, &entry, current, &mut updated) }
            } else {
                status
            };
            let status = if status == ERROR_SUCCESS {
                unsafe {
                    SetSecurityInfo(
                        handle,
                        SE_KERNEL_OBJECT,
                        DACL_SECURITY_INFORMATION,
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        updated,
                        std::ptr::null_mut(),
                    )
                }
            } else {
                status
            };
            unsafe {
                LocalFree(sid);
                if !descriptor.is_null() {
                    LocalFree(descriptor);
                }
                if !updated.is_null() {
                    LocalFree(updated.cast());
                }
            }
            if status != ERROR_SUCCESS {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
        }
    }
    Ok(result)
}
pub fn path_acl(path: &std::path::Path, descriptor: &str) -> Result<(), RacpError> {
    racp_core::validate_local_path(path)?;
    let path: Vec<u16> = path
        .as_os_str()
        .to_string_lossy()
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let text: Vec<u16> = descriptor.encode_utf16().chain(Some(0)).collect();
    let mut security = std::ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            text.as_ptr(),
            SDDL_REVISION_1,
            &mut security,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let success = unsafe {
        SetFileSecurityW(
            path.as_ptr(),
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            security,
        )
    };
    unsafe {
        LocalFree(security);
    }
    if success == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}

pub(super) fn require_owner(path: &std::path::Path, expected: &str) -> Result<(), RacpError> {
    racp_core::validate_local_path(path)?;
    let mut name:Vec<u16>=path.to_string_lossy().encode_utf16().chain(Some(0)).collect();
    let sid_text:Vec<u16>=expected.encode_utf16().chain(Some(0)).collect();
    let mut owner=std::ptr::null_mut();let mut security=std::ptr::null_mut();let mut sid=std::ptr::null_mut();
    if unsafe { ConvertStringSidToSidW(sid_text.as_ptr(),&mut sid) }==0 { return Err(RacpError::new("PERMISSION_DENIED")); }
    let status=unsafe { GetNamedSecurityInfoW(name.as_mut_ptr(),SE_FILE_OBJECT,OWNER_SECURITY_INFORMATION,&mut owner,std::ptr::null_mut(),std::ptr::null_mut(),std::ptr::null_mut(),&mut security) };
    let matches=status==ERROR_SUCCESS && !owner.is_null() && unsafe { EqualSid(owner,sid) }!=0;
    unsafe { LocalFree(sid);if !security.is_null() {LocalFree(security);} }
    if !matches {return Err(RacpError::new("PERMISSION_DENIED"));}
    Ok(())
}
