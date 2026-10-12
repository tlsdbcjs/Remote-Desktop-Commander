//! Grant enrolled controller SIDs only the query rights required to verify the server.
use racp_contract::{service_scope::ServiceScope, RacpError};
use std::{
    ffi::c_void,
    os::windows::io::{FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::{READ_CONTROL, WRITE_DAC},
    System::Threading::*,
};
struct Local(*mut c_void);
impl Drop for Local {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}
pub(crate) fn allow_queries(scope: &ServiceScope) -> Result<(), RacpError> {
    scope.validate()?;
    let peer = crate::providers::desktop::native_identity::PinnedPeer::open(std::process::id())?;
    if peer.identity().sid != scope.service_sid
        || peer.identity().session != 0
        || peer.identity().administrator
        || scope.service_sid != racp_core::service_directory::expected_service_sid()?
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let raw = unsafe {
        OpenProcess(
            READ_CONTROL | WRITE_DAC | PROCESS_QUERY_LIMITED_INFORMATION,
            0,
            std::process::id(),
        )
    };
    if raw.is_null() {
        return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
    }
    let _process = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut token = std::ptr::null_mut();
    if unsafe { OpenProcessToken(raw, TOKEN_QUERY | READ_CONTROL | WRITE_DAC, &mut token) } == 0 {
        return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
    }
    let _token = unsafe { OwnedHandle::from_raw_handle(token) };
    grant(
        raw,
        scope,
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
    )?;
    grant(token, scope, TOKEN_QUERY)?;
    Ok(())
}
fn grant(handle: HANDLE, scope: &ServiceScope, mask: u32) -> Result<(), RacpError> {
    let mut old = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    if unsafe {
        GetSecurityInfo(
            handle,
            SE_KERNEL_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut old,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    } != ERROR_SUCCESS
        || old.is_null()
        || descriptor.is_null()
    {
        return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
    }
    let _descriptor = Local(descriptor);
    let mut sids = vec![];
    let mut entries = vec![];
    for controller in &scope.controllers {
        let text: Vec<u16> = controller.user_sid.encode_utf16().chain(Some(0)).collect();
        let mut sid = std::ptr::null_mut();
        if unsafe { ConvertStringSidToSidW(text.as_ptr(), &mut sid) } == 0 {
            return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
        }
        sids.push(Local(sid));
        entries.push(EXPLICIT_ACCESS_W {
            grfAccessPermissions: mask,
            grfAccessMode: GRANT_ACCESS,
            grfInheritance: NO_INHERITANCE,
            Trustee: TRUSTEE_W {
                pMultipleTrustee: std::ptr::null_mut(),
                MultipleTrusteeOperation: NO_MULTIPLE_TRUSTEE,
                TrusteeForm: TRUSTEE_IS_SID,
                TrusteeType: TRUSTEE_IS_USER,
                ptstrName: sid.cast(),
            },
        });
    }
    let mut next = std::ptr::null_mut();
    if unsafe { SetEntriesInAclW(entries.len() as u32, entries.as_ptr(), old, &mut next) }
        != ERROR_SUCCESS
        || next.is_null()
    {
        return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
    }
    let _next = Local(next.cast());
    if unsafe {
        SetSecurityInfo(
            handle,
            SE_KERNEL_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            next,
            std::ptr::null_mut(),
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
    }
    Ok(())
}
