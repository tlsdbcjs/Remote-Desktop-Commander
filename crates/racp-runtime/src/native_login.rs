//! Local user's fixed native Client autorun entry. No remote registry API or arbitrary command.
use racp_contract::{canonical_digest, digest, local_ipc::reject_duplicate_keys, RacpError};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::Path;
const DOCUMENT: &str = "native-login.json";
const PENDING: &str = "native-login.pending.json";
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Configuration {
    schema_version: u32,
    revision: u64,
    command: Option<String>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    previous: Configuration,
    next: Configuration,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Update {
    pub expected_revision: u64,
    pub expected_value_sha256: String,
    pub enabled: bool,
    pub reconcile: bool,
}
fn load<T: serde::de::DeserializeOwned>(root: &Path, name: &str) -> Result<T, RacpError> {
    let bytes = racp_core::read_bounded(&root.join(name), 65536, true)?;
    reject_duplicate_keys(&bytes)?;
    Ok(serde_json::from_slice(&bytes)?)
}
fn config(root: &Path) -> Result<Configuration, RacpError> {
    let value = if root.join(DOCUMENT).try_exists()? {
        load(root, DOCUMENT)?
    } else {
        Configuration {
            schema_version: 1,
            revision: 0,
            command: None,
        }
    };
    if value.schema_version != 1
        || value
            .command
            .as_ref()
            .is_some_and(|value| value.len() > 16384 || value.contains(['\0', '\r', '\n']))
    {
        return Err(RacpError::new("STATE_SCHEMA_UNSUPPORTED"));
    }
    Ok(value)
}
fn hash(value: &Option<String>) -> String {
    canonical_digest(&json!(value))
}
#[cfg(windows)]
pub fn status(root: &Path) -> Result<Value, RacpError> {
    racp_core::state_schema::NativeState::load(root)?;
    let name = name(root)?;
    let config = config(root)?;
    let current = windows::read(&name)?;
    Ok(
        json!({"revision":config.revision,"enabled":current.is_some(),"owned":current==config.command,
        "value_sha256":hash(&current),"pending":root.join(PENDING).try_exists()?,"current_user_only":true}),
    )
}
#[cfg(windows)]
fn name(root: &Path) -> Result<String, RacpError> {
    let root = racp_core::validate_local_path(root)?;
    let identity = crate::local_ipc::LocalEndpoint::for_root(&root)?;
    Ok(format!(
        "RACP.Native.{}",
        digest(format!(
            "{}:{}",
            identity.user_sid,
            root.to_string_lossy().replace('/', "\\").to_lowercase()
        ))
    ))
}
/// Inspect every native login value of this user, including custom state roots.
/// No command is executed and no unrelated Registry value is returned or changed.
#[cfg(windows)]
pub fn assert_payload_unused(payload: &Path) -> Result<(), RacpError> {
    let payload = racp_core::validate_local_path(payload)?;
    for name in windows::native_names()? {
        let Some(command) = windows::read(&name)? else {
            continue;
        };
        let executable = windows::login_executable(&command)?;
        if racp_core::path_within(&executable, &payload) {
            return Err(RacpError::new("LOGIN_STILL_ENABLED"));
        }
    }
    Ok(())
}
#[cfg(windows)]
pub fn configure(root: &Path, input: Update) -> Result<Value, RacpError> {
    if racp_core::machine_service::is_service_install(&std::env::current_exe()?)? {
        return Err(RacpError::new("SERVICE_INSTALL_USER_AUTORUN_NOT_SUPPORTED"));
    }
    racp_core::state_schema::NativeState::load(root)?;
    let _lock = racp_core::InstanceLock::acquire(&root.join("native-login.lock"))?;
    let name = name(root)?;
    let previous = config(root)?;
    let current = windows::read(&name)?;
    if input.expected_revision != previous.revision || input.expected_value_sha256 != hash(&current)
    {
        return Err(RacpError::new("STATE_REVISION_CONFLICT"));
    }
    if root.join(PENDING).try_exists()? {
        if !input.reconcile {
            return Err(RacpError::new("LOGIN_OUTCOME_UNKNOWN"));
        }
        let intent: Intent = load(root, PENDING)?;
        let before = serde_json::to_value(&intent.previous)? == serde_json::to_value(&previous)?;
        let after = serde_json::to_value(&intent.next)? == serde_json::to_value(&previous)?;
        if (!before && !after)
            || intent.next.revision
                != intent
                    .previous
                    .revision
                    .checked_add(1)
                    .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?
            || intent.previous.schema_version != 1
            || intent.next.schema_version != 1
        {
            return Err(RacpError::new("LOGIN_OUTCOME_UNKNOWN"));
        }
        if current == intent.next.command {
            racp_core::atomic_write(
                &root.join(DOCUMENT),
                &serde_json::to_vec_pretty(&intent.next)?,
                true,
            )?;
        } else if !before || current != intent.previous.command {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        std::fs::remove_file(root.join(PENDING))?;
        return status(root);
    }
    if input.reconcile {
        return status(root);
    }
    if current != previous.command {
        return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
    }
    let executable = std::env::current_exe()?;
    let payload = executable
        .parent()
        .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
    let _payload_pin = if input.enabled {
        Some(racp_core::distribution::pin_payload(payload)?)
    } else {
        None
    };
    let command = if input.enabled {
        let quote = |path: &Path| -> Result<String, RacpError> {
            let text = path
                .to_str()
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
            if text.contains(['"', '\0', '\r', '\n']) {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            // Trailing backslashes must be doubled before the closing CRT quote.
            let trailing = text.chars().rev().take_while(|c| *c == '\\').count();
            Ok(format!("\"{}{}\"", text, "\\".repeat(trailing)))
        };
        Some(format!(
            "{} --state-dir {} --start-agent --require-package",
            quote(&payload.join("racp-client.exe"))?,
            quote(root)?
        ))
    } else {
        None
    };
    if command
        .as_ref()
        .is_some_and(|command| command.len() > 16384)
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    if current == command {
        return status(root);
    }
    let next = Configuration {
        schema_version: 1,
        revision: previous
            .revision
            .checked_add(1)
            .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?,
        command,
    };
    racp_core::atomic_write(
        &root.join(PENDING),
        &serde_json::to_vec_pretty(&Intent {
            previous: previous.clone(),
            next: next.clone(),
        })?,
        true,
    )?;
    // Recheck immediately before commit. Win32 Registry has no compare-exchange.
    if windows::read(&name)? != previous.command {
        return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
    }
    windows::write(&name, next.command.as_deref())
        .map_err(|_| RacpError::new("LOGIN_OUTCOME_UNKNOWN"))?;
    if windows::read(&name)? != next.command {
        return Err(RacpError::new("LOGIN_OUTCOME_UNKNOWN"));
    }
    racp_core::atomic_write(
        &root.join(DOCUMENT),
        &serde_json::to_vec_pretty(&next)?,
        true,
    )
    .map_err(|_| RacpError::new("LOGIN_OUTCOME_UNKNOWN"))?;
    std::fs::remove_file(root.join(PENDING))
        .map_err(|_| RacpError::new("LOGIN_OUTCOME_UNKNOWN"))?;
    status(root)
}
#[cfg(windows)]
mod windows {
    use super::*;
    use windows_sys::Win32::{Foundation::*, System::Registry::*};
    const RUN: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
    pub(super) fn native_names() -> Result<Vec<String>, RacpError> {
        let Some(key) = open(false)? else {
            return Ok(vec![]);
        };
        let mut values = vec![];
        for index in 0..1024 {
            let mut raw = vec![0u16; 16384];
            let mut length = raw.len() as u32;
            let code = unsafe {
                RegEnumValueW(
                    key.0,
                    index,
                    raw.as_mut_ptr(),
                    &mut length,
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                )
            };
            if code == ERROR_NO_MORE_ITEMS {
                return Ok(values);
            }
            if code != ERROR_SUCCESS || length as usize >= raw.len() {
                return Err(RacpError::new("LOGIN_ACCESS_DENIED"));
            }
            let name = String::from_utf16(&raw[..length as usize])
                .map_err(|_| RacpError::new("LOGIN_OWNERSHIP_CONFLICT"))?;
            if name.starts_with("RACP.Native.") {
                values.push(name);
            }
        }
        Err(RacpError::new("RESOURCE_EXHAUSTED"))
    }
    pub(super) fn login_executable(command: &str) -> Result<std::path::PathBuf, RacpError> {
        use windows_sys::Win32::{Foundation::LocalFree, UI::Shell::CommandLineToArgvW};
        if command.is_empty() || command.len() > 16384 || command.contains(['\0', '\r', '\n']) {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        let mut count = 0;
        let raw = unsafe { CommandLineToArgvW(wide(command).as_ptr(), &mut count) };
        struct Arguments(*mut *mut u16);
        impl Drop for Arguments {
            fn drop(&mut self) {
                unsafe {
                    LocalFree(self.0.cast());
                }
            }
        }
        if raw.is_null() {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        let _arguments = Arguments(raw);
        if count != 5 {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        let mut args = vec![];
        for index in 0..count as usize {
            let text = unsafe { *raw.add(index) };
            let mut length = 0;
            while length <= 16384 && unsafe { *text.add(length) } != 0 {
                length += 1;
            }
            if length > 16384 {
                return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
            }
            args.push(
                String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
                    .map_err(|_| RacpError::new("LOGIN_OWNERSHIP_CONFLICT"))?,
            );
        }
        if args[1] != "--state-dir" || args[3] != "--start-agent" || args[4] != "--require-package"
        {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        racp_core::validate_local_path(std::path::Path::new(&args[0]))
            .map_err(|_| RacpError::new("LOGIN_OWNERSHIP_CONFLICT"))
    }
    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(Some(0)).collect()
    }
    struct Key(HKEY);
    impl Drop for Key {
        fn drop(&mut self) {
            unsafe {
                RegCloseKey(self.0);
            }
        }
    }
    fn open(write: bool) -> Result<Option<Key>, RacpError> {
        let mut key = std::ptr::null_mut();
        let access = KEY_QUERY_VALUE | if write { KEY_SET_VALUE } else { 0 };
        let code = if write {
            unsafe {
                RegCreateKeyExW(
                    HKEY_CURRENT_USER,
                    wide(RUN).as_ptr(),
                    0,
                    std::ptr::null(),
                    REG_OPTION_NON_VOLATILE,
                    access,
                    std::ptr::null(),
                    &mut key,
                    std::ptr::null_mut(),
                )
            }
        } else {
            unsafe { RegOpenKeyExW(HKEY_CURRENT_USER, wide(RUN).as_ptr(), 0, access, &mut key) }
        };
        if code == ERROR_FILE_NOT_FOUND {
            return Ok(None);
        }
        if code != ERROR_SUCCESS {
            return Err(RacpError::new("LOGIN_ACCESS_DENIED"));
        }
        Ok(Some(Key(key)))
    }
    pub fn read(name: &str) -> Result<Option<String>, RacpError> {
        let Some(key) = open(false)? else {
            return Ok(None);
        };
        let name = wide(name);
        let mut size = 0;
        let mut kind = 0;
        let code = unsafe {
            RegQueryValueExW(
                key.0,
                name.as_ptr(),
                std::ptr::null_mut(),
                &mut kind,
                std::ptr::null_mut(),
                &mut size,
            )
        };
        if code == ERROR_FILE_NOT_FOUND {
            return Ok(None);
        }
        if code != ERROR_SUCCESS || kind != REG_SZ || size < 2 || size > 32768 || size % 2 != 0 {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        let mut value = vec![0u16; size as usize / 2];
        let code = unsafe {
            RegQueryValueExW(
                key.0,
                name.as_ptr(),
                std::ptr::null_mut(),
                &mut kind,
                value.as_mut_ptr().cast(),
                &mut size,
            )
        };
        if code != ERROR_SUCCESS
            || kind != REG_SZ
            || size < 2
            || size % 2 != 0
            || size as usize > value.len() * 2
        {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        value.truncate(size as usize / 2);
        if value.pop() != Some(0) || value.contains(&0) {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        Ok(Some(
            String::from_utf16(&value).map_err(|_| RacpError::new("LOGIN_OWNERSHIP_CONFLICT"))?,
        ))
    }
    pub fn write(name: &str, value: Option<&str>) -> Result<(), RacpError> {
        let key = open(true)?.ok_or_else(|| RacpError::new("LOGIN_ACCESS_DENIED"))?;
        let name = wide(name);
        let code = match value {
            Some(value) => {
                let data = wide(value);
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
            }
            None => unsafe { RegDeleteValueW(key.0, name.as_ptr()) },
        };
        if code != ERROR_SUCCESS && !(value.is_none() && code == ERROR_FILE_NOT_FOUND) {
            return Err(RacpError::new("LOGIN_ACCESS_DENIED"));
        }
        Ok(())
    }
}
