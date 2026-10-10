//! OS-derived peer identity. The process handle remains pinned for the entire exchange.
use super::PeerIdentity;
use racp_contract::RacpError;
use std::{
    collections::BTreeSet,
    mem::size_of,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::{Foundation::*, Security::*, System::Threading::*};

pub struct PinnedPeer {
    process: OwnedHandle,
    identity: PeerIdentity,
}
struct TokenBuffer {
    words: Vec<usize>,
    length: usize,
}
impl TokenBuffer {
    fn query(token: HANDLE, class: TOKEN_INFORMATION_CLASS) -> Result<Self, RacpError> {
        let mut length = 0;
        unsafe {
            GetTokenInformation(token, class, std::ptr::null_mut(), 0, &mut length);
        }
        if !(4..=1024 * 1024).contains(&length) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let mut result = Self {
            words: vec![0; (length as usize).div_ceil(size_of::<usize>())],
            length: length as usize,
        };
        if unsafe {
            GetTokenInformation(
                token,
                class,
                result.words.as_mut_ptr().cast(),
                length,
                &mut length,
            )
        } == 0
            || length as usize > result.length
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        result.length = length as usize;
        Ok(result)
    }
    fn read<T: Copy>(&self, offset: usize) -> Result<T, RacpError> {
        if offset
            .checked_add(size_of::<T>())
            .is_none_or(|end| end > self.length)
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(
            unsafe {
                std::ptr::read_unaligned(self.words.as_ptr().cast::<u8>().add(offset).cast())
            },
        )
    }
    fn sid(&self, ptr: PSID) -> Result<String, RacpError> {
        let base = self.words.as_ptr() as usize;
        let offset = (ptr as usize)
            .checked_sub(base)
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        let header: [u8; 8] = self.read(offset)?;
        if header[0] != 1 || header[1] > 15 {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let mut authority = 0u64;
        for byte in &header[2..] {
            authority = (authority << 8) | u64::from(*byte);
        }
        let mut value = format!("S-1-{authority}");
        for index in 0..header[1] as usize {
            let bytes: [u8; 4] = self.read(offset + 8 + index * 4)?;
            value.push_str(&format!("-{}", u32::from_le_bytes(bytes)));
        }
        if !super::pairing::sid(&value) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(value)
    }
}
impl PinnedPeer {
    pub fn open(pid: u32) -> Result<Self, RacpError> {
        if pid == 0 {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let raw = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                pid,
            )
        };
        if raw.is_null() {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let process = unsafe { OwnedHandle::from_raw_handle(raw) };
        if unsafe { WaitForSingleObject(raw, 0) } != WAIT_TIMEOUT {
            return Err(RacpError::new("PROCESS_NOT_FOUND"));
        }
        let mut birth = FILETIME::default();
        let mut exit = birth;
        let mut kernel = birth;
        let mut user = birth;
        if unsafe { GetProcessTimes(raw, &mut birth, &mut exit, &mut kernel, &mut user) } == 0 {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let ticks = ((birth.dwHighDateTime as u64) << 32) | birth.dwLowDateTime as u64;
        let created = (ticks / 10_000_000) as f64 + (ticks % 10_000_000) as f64 / 10_000_000.0
            - 11_644_473_600.0;
        let mut token = std::ptr::null_mut();
        if unsafe { OpenProcessToken(raw, TOKEN_QUERY, &mut token) } == 0 {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(token) };
        let token = token.as_raw_handle();
        let user = TokenBuffer::query(token, TokenUser)?;
        let sid = user.sid(user.read::<TOKEN_USER>(0)?.User.Sid)?;
        let session = TokenBuffer::query(token, TokenSessionId)?.read::<u32>(0)?;
        let mandatory = TokenBuffer::query(token, TokenIntegrityLevel)?;
        let label = mandatory.sid(mandatory.read::<TOKEN_MANDATORY_LABEL>(0)?.Label.Sid)?;
        let integrity = label
            .strip_prefix("S-1-16-")
            .and_then(|v| v.parse::<u32>().ok())
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        let groups = TokenBuffer::query(token, TokenGroups)?;
        let count = groups.read::<u32>(0)? as usize;
        let offset = std::mem::offset_of!(TOKEN_GROUPS, Groups);
        if count > 4096 || offset + count * size_of::<SID_AND_ATTRIBUTES>() > groups.length {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let mut service_sids = BTreeSet::new();
        let mut administrator = false;
        for index in 0..count {
            let group = groups
                .read::<SID_AND_ATTRIBUTES>(offset + index * size_of::<SID_AND_ATTRIBUTES>())?;
            if group.Attributes & SE_GROUP_ENABLED == 0
                || group.Attributes & SE_GROUP_USE_FOR_DENY_ONLY != 0
            {
                continue;
            }
            let sid = groups.sid(group.Sid)?;
            administrator |= sid == "S-1-5-32-544";
            if sid
                .strip_prefix("S-1-5-80-")
                .is_some_and(|tail| tail.split('-').count() == 5)
            {
                service_sids.insert(sid);
            }
        }
        let peer = Self {
            process,
            identity: PeerIdentity {
                pid,
                created,
                sid,
                session,
                integrity,
                service_sids,
                administrator,
            },
        };
        peer.alive()?;
        Ok(peer)
    }
    pub fn identity(&self) -> &PeerIdentity {
        &self.identity
    }
    pub fn alive(&self) -> Result<(), RacpError> {
        if unsafe { WaitForSingleObject(self.process.as_raw_handle(), 0) } == WAIT_TIMEOUT {
            Ok(())
        } else {
            Err(RacpError::new("PROCESS_NOT_FOUND"))
        }
    }
}
