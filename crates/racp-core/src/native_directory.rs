//! A fresh native state directory is private at creation. Existing shared roots are rejected.
use crate::{validate_local_path, Workspaces};
use racp_contract::RacpError;
use std::path::Path;
#[cfg(windows)]
pub(crate) fn current_sid() -> Result<String, RacpError> {
    windows::actor().map(|(_, sid)| sid)
}
pub fn create(root: &Path) -> Result<(), RacpError> {
    validate_local_path(root)?;
    if root.try_exists()? {
        return verify(root);
    }
    let parent = root
        .parent()
        .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
    crate::private_dir(parent)?;
    let guards = Workspaces::new(parent, &[])?;
    let _pin = guards.directory("default", parent)?;
    #[cfg(windows)]
    windows::create(root)?;
    #[cfg(not(windows))]
    crate::private_dir(root)?;
    verify(root)
}
pub fn verify(root: &Path) -> Result<(), RacpError> {
    validate_local_path(root)?;
    let guard = Workspaces::new(root, &[])?;
    let directory = guard.directory("default", root)?;
    #[cfg(windows)]
    {
        windows::verify(&directory)
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let info = directory.handle().metadata()?;
        if info.uid() != unsafe { libc::geteuid() } || info.mode() & 0o077 != 0 {
            Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"))
        } else {
            Ok(())
        }
    }
}
#[cfg(windows)]
mod windows {
    use super::*;
    use std::{
        ffi::c_void,
        os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    };
    use windows_sys::Win32::{
        Foundation::{LocalFree, ERROR_SUCCESS},
        Security::{Authorization::*, *},
        Storage::FileSystem::{CreateDirectoryW, FILE_ALL_ACCESS},
        System::Threading::{GetCurrentProcess, OpenProcessToken},
    };
    struct Local(*mut c_void);
    impl Drop for Local {
        fn drop(&mut self) {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }
    pub(super) fn actor() -> Result<(Vec<usize>, String), RacpError> {
        let mut token = std::ptr::null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
        }
        let _token = unsafe { OwnedHandle::from_raw_handle(token) };
        let mut size = 0;
        unsafe {
            GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut size);
        }
        if size < std::mem::size_of::<TOKEN_USER>() as u32 || size > 65536 {
            return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
        }
        let mut words = vec![0usize; (size as usize).div_ceil(std::mem::size_of::<usize>())];
        if unsafe {
            GetTokenInformation(token, TokenUser, words.as_mut_ptr().cast(), size, &mut size)
        } == 0
        {
            return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
        }
        let user = unsafe { &*words.as_ptr().cast::<TOKEN_USER>() };
        let mut text = std::ptr::null_mut();
        if unsafe { ConvertSidToStringSidW(user.User.Sid, &mut text) } == 0 {
            return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
        }
        let _text = Local(text.cast());
        let mut length = 0;
        while length < 184 && unsafe { *text.add(length) } != 0 {
            length += 1;
        }
        if length >= 184 {
            return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
        }
        let sid = String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
            .map_err(|_| RacpError::new("LOCAL_IDENTITY_FAILED"))?;
        Ok((words, sid))
    }
    pub(super) fn create(root: &Path) -> Result<(), RacpError> {
        let (_, sid) = actor()?;
        let sddl = wide(&format!("O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)"));
        let mut descriptor = std::ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"));
        }
        let _descriptor = Local(descriptor);
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        if unsafe { CreateDirectoryW(wide(&root.to_string_lossy()).as_ptr(), &attributes) } == 0 {
            return Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"));
        }
        Ok(())
    }
    pub(super) fn verify(directory: &crate::Directory) -> Result<(), RacpError> {
        let (actor, _) = actor()?;
        let user = unsafe { &*actor.as_ptr().cast::<TOKEN_USER>() };
        let mut system = std::ptr::null_mut();
        if unsafe { ConvertStringSidToSidW(wide("S-1-5-18").as_ptr(), &mut system) } == 0 {
            return Err(RacpError::new("LOCAL_IDENTITY_FAILED"));
        }
        let _system = Local(system);
        let mut owner = std::ptr::null_mut();
        let mut dacl = std::ptr::null_mut();
        let mut descriptor = std::ptr::null_mut();
        if unsafe {
            GetSecurityInfo(
                directory.handle().as_raw_handle(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                std::ptr::null_mut(),
                &mut dacl,
                std::ptr::null_mut(),
                &mut descriptor,
            )
        } != ERROR_SUCCESS
            || descriptor.is_null()
        {
            return Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"));
        }
        let _descriptor = Local(descriptor);
        let mut control = 0;
        let mut revision = 0;
        if owner.is_null()
            || unsafe { EqualSid(owner, user.User.Sid) } == 0
            || dacl.is_null()
            || unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) } == 0
            || control & SE_DACL_PROTECTED == 0
            || unsafe { (*dacl).AceCount } != 2
        {
            return Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"));
        }
        let mut actor_seen = false;
        let mut system_seen = false;
        for index in 0..2 {
            let mut raw = std::ptr::null_mut();
            if unsafe { GetAce(dacl, index, &mut raw) } == 0 || raw.is_null() {
                return Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"));
            }
            let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
            // ACCESS_ALLOWED_ACE_TYPE is the SDK wire value 0.
            if ace.Header.AceType != 0
                || ace.Mask != FILE_ALL_ACCESS
                || ace.Header.AceFlags != (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8
            {
                return Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"));
            }
            let sid = (&ace.SidStart as *const u32).cast_mut().cast();
            if unsafe { EqualSid(sid, user.User.Sid) } != 0 {
                actor_seen = true;
            } else if unsafe { EqualSid(sid, system) } == 0 {
                return Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"));
            }
            if unsafe { EqualSid(sid, system) } != 0 {
                system_seen = true;
            }
        }
        if actor_seen && system_seen {
            Ok(())
        } else {
            Err(RacpError::new("STATE_DIRECTORY_NOT_PRIVATE"))
        }
    }
}
