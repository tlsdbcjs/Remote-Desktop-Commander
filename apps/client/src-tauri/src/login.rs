//! Per-user startup entry retains Electron's established name and startup argument.
use racp_contract::RacpError;
use serde_json::{json, Value};
#[cfg(windows)]
use windows_sys::Win32::{Foundation::*, System::Registry::*};
pub const ARGUMENT: &str = "racp-background-agent";
#[cfg(windows)]
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}
#[cfg(windows)]
struct Key(HKEY);
#[cfg(windows)]
impl Drop for Key {
    fn drop(&mut self) {
        unsafe {
            RegCloseKey(self.0);
        }
    }
}
#[cfg(windows)]
fn key() -> Result<Key, RacpError> {
    let mut handle = std::ptr::null_mut();
    if unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            wide("Software\\Microsoft\\Windows\\CurrentVersion\\Run").as_ptr(),
            0,
            std::ptr::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_QUERY_VALUE | KEY_SET_VALUE,
            std::ptr::null(),
            &mut handle,
            std::ptr::null_mut(),
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("LOGIN_FAILED"));
    }
    Ok(Key(handle))
}
#[cfg(windows)]
fn expected() -> Result<String, RacpError> {
    let path = std::env::current_exe()?;
    racp_core::validate_local_path(&path)?;
    let path = path
        .to_str()
        .filter(|p| !p.contains('"'))
        .ok_or_else(|| RacpError::new("LOGIN_FAILED"))?;
    Ok(format!("\"{path}\" {ARGUMENT}"))
}
#[cfg(windows)]
fn entry(key: &Key) -> Result<Option<String>, RacpError> {
    let mut kind = 0;
    let mut size = 0;
    let name = wide("app.racp.client");
    let code = unsafe {
        RegQueryValueExW(
            key.0,
            name.as_ptr(),
            std::ptr::null(),
            &mut kind,
            std::ptr::null_mut(),
            &mut size,
        )
    };
    if code == ERROR_FILE_NOT_FOUND {
        return Ok(None);
    }
    if code != ERROR_SUCCESS || kind != REG_SZ || size > 65536 || size % 2 != 0 {
        return Err(RacpError::new("LOGIN_FAILED"));
    }
    let mut buffer = vec![0u16; size as usize / 2];
    if unsafe {
        RegQueryValueExW(
            key.0,
            name.as_ptr(),
            std::ptr::null(),
            &mut kind,
            buffer.as_mut_ptr().cast(),
            &mut size,
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("LOGIN_FAILED"));
    }
    let end = buffer.iter().position(|u| *u == 0).unwrap_or(buffer.len());
    Ok(Some(String::from_utf16_lossy(&buffer[..end])))
}
pub fn settings() -> Result<Value, RacpError> {
    #[cfg(windows)]
    {
        let key = key()?;
        let current = entry(&key)?;
        let expected = expected()?;
        let registered = current.as_deref() == Some(expected.as_str());
        let mut disabled = false;
        let path =
            wide("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run");
        let mut approved = std::ptr::null_mut();
        if unsafe {
            RegOpenKeyExW(
                HKEY_CURRENT_USER,
                path.as_ptr(),
                0,
                KEY_QUERY_VALUE,
                &mut approved,
            )
        } == ERROR_SUCCESS
        {
            let approved = Key(approved);
            let mut data = [0u8; 64];
            let mut size = 64;
            let mut kind = 0;
            if unsafe {
                RegQueryValueExW(
                    approved.0,
                    wide("app.racp.client").as_ptr(),
                    std::ptr::null(),
                    &mut kind,
                    data.as_mut_ptr(),
                    &mut size,
                )
            } == ERROR_SUCCESS
                && kind == REG_BINARY
                && size >= 4
            {
                disabled = matches!(data[0], 3 | 7);
            }
        }
        Ok(
            json!({"available":!cfg!(debug_assertions),"registered":registered,"enabled":registered&&!disabled,"owned":current.is_none()||registered}),
        )
    }
    #[cfg(not(windows))]
    {
        Ok(json!({"available":false,"registered":false,"enabled":false}))
    }
}
pub fn set(enabled: bool) -> Result<Value, RacpError> {
    #[cfg(windows)]
    {
        if cfg!(debug_assertions) {
            return Err(RacpError::new("LOGIN_UNAVAILABLE"));
        }
        let key = key()?;
        let current = entry(&key)?;
        let expected = expected()?;
        if current.as_ref().is_some_and(|s| s != &expected) {
            return Err(RacpError::new("LOGIN_OWNERSHIP_MISMATCH"));
        }
        let name = wide("app.racp.client");
        let code = if enabled {
            let data = wide(&expected);
            unsafe {
                RegSetValueExW(
                    key.0,
                    name.as_ptr(),
                    0,
                    REG_SZ,
                    data.as_ptr().cast(),
                    (data.len() * 2) as u32,
                )
            }
        } else {
            unsafe { RegDeleteValueW(key.0, name.as_ptr()) }
        };
        if code != ERROR_SUCCESS && !(code == ERROR_FILE_NOT_FOUND && !enabled) {
            return Err(RacpError::new("LOGIN_FAILED"));
        }
        if enabled {
            let mut approved = std::ptr::null_mut();
            let path = wide(r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run");
            let code = unsafe { RegOpenKeyExW(HKEY_CURRENT_USER,path.as_ptr(),0,KEY_SET_VALUE,&mut approved) };
            if code==ERROR_SUCCESS {
                let approved=Key(approved);
                let code=unsafe { RegDeleteValueW(approved.0,name.as_ptr()) };
                if code!=ERROR_SUCCESS && code!=ERROR_FILE_NOT_FOUND { return Err(RacpError::new("LOGIN_FAILED")); }
            } else if code!=ERROR_FILE_NOT_FOUND { return Err(RacpError::new("LOGIN_FAILED")); }
        }
        let result = settings()?;
        if result["registered"] != enabled || enabled && result["enabled"] != true {
            return Err(RacpError::new("LOGIN_FAILED"));
        }
        Ok(result)
    }
    #[cfg(not(windows))]
    {
        let _ = enabled;
        Err(RacpError::new("LOGIN_UNAVAILABLE"))
    }
}
