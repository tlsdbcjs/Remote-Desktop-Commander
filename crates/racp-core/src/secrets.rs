use crate::paths::{atomic_write, read_bounded};
use racp_contract::RacpError;
use std::{collections::BTreeMap, path::PathBuf};
pub struct SecretStore {
    path: PathBuf,
}
impl SecretStore {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }
    pub fn load(&self) -> Result<BTreeMap<String, String>, RacpError> {
        let raw = unprotect(&read_bounded(&self.path, 65536, true)?)?;
        let value =
            serde_json::from_slice(&raw).map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        Ok(value)
    }
    pub fn save(&self, value: &BTreeMap<String, String>, overwrite: bool) -> Result<(), RacpError> {
        let raw = serde_json::to_vec(value)?;
        if raw.len() > 32768 {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        let raw = protect(&raw)?;
        if raw.len() > 65536 {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        atomic_write(&self.path, &raw, overwrite)
    }
}
#[cfg(not(windows))]
fn protect(raw: &[u8]) -> Result<Vec<u8>, RacpError> {
    Ok(raw.to_vec())
}
#[cfg(not(windows))]
fn unprotect(raw: &[u8]) -> Result<Vec<u8>, RacpError> {
    Ok(raw.to_vec())
}
#[cfg(windows)]
fn protect(raw: &[u8]) -> Result<Vec<u8>, RacpError> {
    dpapi(raw, true)
}
#[cfg(windows)]
fn unprotect(raw: &[u8]) -> Result<Vec<u8>, RacpError> {
    dpapi(raw, false)
}
#[cfg(windows)]
fn dpapi(raw: &[u8], encrypt: bool) -> Result<Vec<u8>, RacpError> {
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::Cryptography::{CryptProtectData, CryptUnprotectData, CRYPT_INTEGER_BLOB},
    };
    let input = CRYPT_INTEGER_BLOB {
        cbData: raw.len() as u32,
        pbData: raw.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: std::ptr::null_mut(),
    };
    let description: Vec<u16> = "RACP\0".encode_utf16().collect();
    let ok = unsafe {
        if encrypt {
            CryptProtectData(
                &input,
                description.as_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                std::ptr::null_mut(),
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null(),
                1,
                &mut output,
            )
        }
    };
    if ok == 0 {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    let result =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec() };
    unsafe {
        LocalFree(output.pbData as *mut _);
    };
    Ok(result)
}
