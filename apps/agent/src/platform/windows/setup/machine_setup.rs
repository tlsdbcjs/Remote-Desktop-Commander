//! Elevated administrator handoff to one fixed, fresh, LocalSystem setup service.
//! Fixed provisioning/config/session-recovery tasks; no Agent run or enrollment.
use racp_contract::{digest, local_ipc::reject_duplicate_keys, new_id, RacpError};
use racp_core::machine_service as machine;
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::OnceLock,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::Services::*,
};
const NAME: &str = "RACPNativeSetup";
struct Service(SC_HANDLE);
impl Drop for Service {
    fn drop(&mut self) {
        unsafe {
            CloseServiceHandle(self.0);
        }
    }
}
struct Local(*mut core::ffi::c_void);
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
fn setup_parent() -> Result<PathBuf, RacpError> {
    let parent = machine::data_parent()?;
    let program_data = parent
        .parent()
        .ok_or_else(|| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))?;
    Ok(program_data.join("RACP-Native-Setup"))
}
fn secure_directory(root: &Path) -> Result<racp_core::Directory, RacpError> {
    if !root.try_exists()? {
        let parent = root
            .parent()
            .ok_or_else(|| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))?;
        let _parent = machine::pin_secure_parent(parent)?;
        let mut descriptor = std::ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                wide("O:BAD:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)").as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
        }
        let _descriptor = Local(descriptor);
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        if unsafe { CreateDirectoryW(wide(&root.to_string_lossy()).as_ptr(), &attributes) } == 0 {
            return Err(RacpError::new("MACHINE_SETUP_CONFLICT"));
        }
    }
    machine::pin_secure_parent(root)
}
pub(super) fn administrator() -> Result<(), RacpError> {
    let actor =
        racp_runtime::providers::desktop::native_identity::PinnedPeer::open(std::process::id())?;
    if !actor.identity().administrator || actor.identity().integrity < 12288 {
        return Err(RacpError::new("MACHINE_SETUP_ELEVATION_REQUIRED"));
    }
    Ok(())
}
fn status(service: &Service) -> Result<SERVICE_STATUS_PROCESS, RacpError> {
    let mut status = SERVICE_STATUS_PROCESS::default();
    let mut bytes = 0;
    if unsafe {
        QueryServiceStatusEx(
            service.0,
            SC_STATUS_PROCESS_INFO,
            (&mut status as *mut SERVICE_STATUS_PROCESS).cast(),
            std::mem::size_of_val(&status) as u32,
            &mut bytes,
        )
    } == 0
    {
        return Err(RacpError::new("MACHINE_SETUP_STATUS_UNKNOWN"));
    }
    Ok(status)
}
fn owner_admin(file: &std::fs::File) -> Result<(), RacpError> {
    use std::os::windows::io::AsRawHandle;
    let mut sid = std::ptr::null_mut();
    if unsafe { ConvertStringSidToSidW(wide("S-1-5-32-544").as_ptr(), &mut sid) } == 0 {
        return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
    }
    let _sid = Local(sid);
    if unsafe {
        SetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION,
            sid,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
    }
    Ok(())
}
fn input(root: &Path, hash: &str) -> Result<Vec<u8>, RacpError> {
    use std::{io::Read, os::windows::io::AsRawHandle};
    let directory = machine::pin_secure_parent(root)?;
    let mut file = directory.open_pinned_read("request.json")?;
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
    {
        return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
    }
    let _descriptor = Local(descriptor);
    let sid_text = |sid: PSID| -> Result<String, RacpError> {
        let mut text = std::ptr::null_mut();
        if sid.is_null() || unsafe { ConvertSidToStringSidW(sid, &mut text) } == 0 {
            return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
        }
        let _text = Local(text.cast());
        let mut len = 0;
        while len < 128 && unsafe { *text.add(len) } != 0 {
            len += 1;
        }
        if len == 128 {
            return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
        }
        String::from_utf16(unsafe { std::slice::from_raw_parts(text, len) })
            .map_err(|_| RacpError::new("MACHINE_SETUP_ACCESS_DENIED"))
    };
    if !["S-1-5-18", "S-1-5-32-544"].contains(&sid_text(owner)?.as_str())
        || acl.is_null()
        || unsafe { (*acl).AceCount } != 2
    {
        return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
    }
    let mut expected =
        std::collections::BTreeSet::from(["S-1-5-18".to_owned(), "S-1-5-32-544".to_owned()]);
    for index in 0..2 {
        let mut raw = std::ptr::null_mut();
        if unsafe { GetAce(acl, index, &mut raw) } == 0 || raw.is_null() {
            return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
        }
        let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
        if ace.Header.AceType != 0
            || ace.Header.AceFlags & INHERITED_ACE as u8 == 0
            || ace.Header.AceFlags
                & !((INHERITED_ACE | OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8)
                != 0
            || ace.Mask != FILE_ALL_ACCESS
            || !expected.remove(&sid_text((&ace.SidStart as *const u32).cast_mut().cast())?)
        {
            return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
        }
    }
    let mut raw = vec![];
    (&mut file).take(65537).read_to_end(&mut raw)?;
    if raw.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    if digest(&raw) != hash {
        return Err(RacpError::new("MACHINE_SETUP_INPUT_CHANGED"));
    }
    reject_duplicate_keys(&raw)?;
    Ok(raw)
}
fn validate_request(raw: &[u8]) -> Result<(), RacpError> {
    if raw.is_empty() || raw.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    reject_duplicate_keys(raw)?;
    let value: Value = serde_json::from_slice(raw)?;
    if let Some(action) = value["type"].as_str() {
        let object = value
            .as_object()
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        match action {
            "restore_service"
                if object.len() == 4
                    && object.keys().all(|key| {
                        [
                            "type",
                            "expected_config_sha256",
                            "expected_device_id",
                            "expected_bootstrap_sha256",
                        ]
                        .contains(&key.as_str())
                    }) =>
            {
                return Ok(())
            }
            "remove_service"
                if object.len() == 3
                    && object.keys().all(|key| {
                        ["type", "expected_config_sha256", "expected_device_id"]
                            .contains(&key.as_str())
                    }) =>
            {
                return Ok(())
            }
            "recover_sessions" | "reconcile" if object.len() == 1 => return Ok(()),
            "configure"
                if object.len() == 4
                    && object.keys().all(|key| {
                        [
                            "type",
                            "source_file",
                            "source_sha256",
                            "expected_staged_sha256",
                        ]
                        .contains(&key.as_str())
                    }) =>
            {
                if value["source_file"].as_str().is_none()
                    || value["source_sha256"].as_str().is_none()
                    || !(value["expected_staged_sha256"].is_null()
                        || value["expected_staged_sha256"].is_string())
                {
                    return Err(RacpError::new("REQUEST_INVALID"));
                }
                return Ok(());
            }
            _ => return Err(RacpError::new("REQUEST_INVALID")),
        }
    }
    if value.as_object().is_none_or(|object| {
        object.len() != 2
            || !object.contains_key("controllers")
            || !object.contains_key("enrollment")
    }) {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let controllers = serde_json::from_value(value["controllers"].clone())?;
    racp_contract::service_scope::ServiceScope {
        service_name: racp_contract::service_scope::SERVICE_NAME.into(),
        service_sid: "S-1-5-80-0-0-0-0-0".into(),
        controllers,
    }
    .validate()?;
    let enrollment: racp_runtime::native_client::Enrollment =
        serde_json::from_value(value["enrollment"].clone())?;
    if enrollment.policy.revision != 0 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    enrollment.policy.permissions()?;
    racp_core::load_connection(
        &enrollment.connection_file,
        Some(&enrollment.connection_sha256.to_ascii_lowercase()),
    )?;
    Ok(())
}
pub fn execute(state: &Path, raw: &[u8]) -> Result<Value, RacpError> {
    administrator()?;
    let expected = machine::data_parent()?.join("state");
    if racp_core::validate_local_path(state)? != expected {
        return Err(RacpError::new("MACHINE_SETUP_CONFLICT"));
    }
    validate_request(raw)?;
    let _payload = racp_core::distribution::pin_current_agent_service_payload()?;
    let exe = std::env::current_exe()?;
    let _image = machine::pin_image_directory(
        exe.parent()
            .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?,
    )?;
    let manager = unsafe {
        OpenSCManagerW(
            std::ptr::null(),
            std::ptr::null(),
            SC_MANAGER_CONNECT | SC_MANAGER_CREATE_SERVICE,
        )
    };
    if manager.is_null() {
        return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
    }
    let manager = Service(manager);
    let prior = unsafe { OpenServiceW(manager.0, wide(NAME).as_ptr(), SERVICE_QUERY_CONFIG) };
    if !prior.is_null() {
        drop(Service(prior));
        return Err(RacpError::new("MACHINE_SETUP_CONFLICT"));
    }
    if unsafe { GetLastError() } != ERROR_SERVICE_DOES_NOT_EXIST {
        return Err(RacpError::new("MACHINE_SETUP_STATUS_UNKNOWN"));
    }
    let parent = setup_parent()?;
    let parent_pin = secure_directory(&parent)?;
    if parent_pin.names()?.len() >= 64 {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let nonce = new_id("setup");
    let root = parent.join(&nonce);
    if root.try_exists()? {
        return Err(RacpError::new("MACHINE_SETUP_CONFLICT"));
    }
    let directory = secure_directory(&root)?;
    let hash = digest(raw);
    racp_core::atomic_write(&root.join("request.json"), raw, false)?;
    // Even the unelevated token of this administrator must not own the privileged input.
    use std::os::windows::fs::OpenOptionsExt;
    let writable = std::fs::OpenOptions::new()
        .access_mode(READ_CONTROL | WRITE_OWNER)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(root.join("request.json"))?;
    owner_admin(&writable)?;
    drop(writable);
    let _request = directory.open_pinned_read("request.json")?;
    if input(&root, &hash)? != raw {
        return Err(RacpError::new("MACHINE_SETUP_INPUT_CHANGED"));
    }
    let quote = |value: &Path| -> Result<String, RacpError> {
        let text = value.to_string_lossy();
        if text.contains(['"', '\r', '\n', '\0']) {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        Ok(format!("\"{text}\""))
    };
    let image = format!(
        "{} native-setup-host --setup-root {} --input-sha256 {}",
        quote(&exe)?,
        quote(&root)?,
        hash
    );
    let service = unsafe {
        CreateServiceW(
            manager.0,
            wide(NAME).as_ptr(),
            wide(NAME).as_ptr(),
            SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | SERVICE_START | DELETE,
            SERVICE_WIN32_OWN_PROCESS,
            SERVICE_DEMAND_START,
            SERVICE_ERROR_NORMAL,
            wide(&image).as_ptr(),
            std::ptr::null(),
            std::ptr::null_mut(),
            std::ptr::null(),
            wide("LocalSystem").as_ptr(),
            std::ptr::null(),
        )
    };
    if service.is_null() {
        return Err(RacpError::new("MACHINE_SETUP_CREATE_UNKNOWN"));
    }
    let service = Service(service);
    if unsafe { StartServiceW(service.0, 0, std::ptr::null()) } == 0 {
        return Err(RacpError::new("MACHINE_SETUP_START_UNKNOWN"));
    }
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        let observed = status(&service)?;
        if observed.dwCurrentState == SERVICE_STOPPED && observed.dwProcessId == 0 {
            if let Ok(receipt) = racp_core::read_bounded(&root.join("result.json"), 16384, false) {
                reject_duplicate_keys(&receipt)?;
                let value: Value = serde_json::from_slice(&receipt)?;
                if value["input_sha256"] == hash
                    && value["request_id"] == nonce
                    && value["format_version"] == 1
                {
                    if observed.dwWin32ExitCode != 0 || value["complete"] != true {
                        return Err(RacpError::new("MACHINE_SETUP_OUTCOME_UNKNOWN"));
                    }
                    if unsafe { DeleteService(service.0) } == 0 {
                        return Err(RacpError::new("MACHINE_SETUP_CLEANUP_UNKNOWN"));
                    }
                    drop(_request);
                    directory.unlink("request.json", false)?;
                    return Ok(value);
                }
            }
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("MACHINE_SETUP_OUTCOME_UNKNOWN"));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// Explicit removal of this package's stopped setup helper only. The target Agent/state are retained.
pub fn cleanup(raw: &[u8]) -> Result<Value, RacpError> {
    administrator()?;
    if raw.len() > 4096 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    reject_duplicate_keys(raw)?;
    let value: Value = serde_json::from_slice(raw)?;
    if value.as_object().is_none_or(|object| {
        object.len() != 2
            || object
                .keys()
                .any(|key| !["setup_root", "input_sha256"].contains(&key.as_str()))
    }) {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let root = racp_core::validate_local_path(Path::new(
        value["setup_root"]
            .as_str()
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?,
    ))?;
    let hash = value["input_sha256"]
        .as_str()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    let nonce = root
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("");
    if root.parent() != Some(setup_parent()?.as_path())
        || !nonce.strip_prefix("setup_").is_some_and(|id| {
            id.len() == 32
                && id
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        || hash.len() != 64
        || !hash
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let _payload = racp_core::distribution::pin_current_agent_service_payload()?;
    let exe = std::env::current_exe()?;
    let _image = machine::pin_image_directory(
        exe.parent()
            .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?,
    )?;
    let directory = machine::pin_secure_parent(&root)?;
    if directory
        .names()?
        .iter()
        .any(|name| !["request.json", "result.json", "cleanup.json"].contains(&name.as_str()))
    {
        return Err(RacpError::new("MACHINE_SETUP_CONTENT_CONFLICT"));
    }
    let request = input(&root, hash)?;
    let manager = unsafe { OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_CONNECT) };
    if manager.is_null() {
        return Err(RacpError::new("MACHINE_SETUP_ACCESS_DENIED"));
    }
    let manager = Service(manager);
    let raw = unsafe {
        OpenServiceW(
            manager.0,
            wide(NAME).as_ptr(),
            SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | DELETE,
        )
    };
    let delete_requested = if raw.is_null() {
        if unsafe { GetLastError() } != ERROR_SERVICE_DOES_NOT_EXIST {
            return Err(RacpError::new("MACHINE_SETUP_STATUS_UNKNOWN"));
        }
        false
    } else {
        let service = Service(raw);
        let mut words = vec![0usize; 16384 / std::mem::size_of::<usize>()];
        let mut needed = 0;
        let pointer = words.as_mut_ptr().cast::<QUERY_SERVICE_CONFIGW>();
        if unsafe { QueryServiceConfigW(service.0, pointer, 16384, &mut needed) } == 0
            || needed > 16384
        {
            return Err(RacpError::new("MACHINE_SETUP_OWNERSHIP_CONFLICT"));
        }
        let config = unsafe { &*pointer };
        let text = |pointer: *const u16| -> Result<String, RacpError> {
            if pointer.is_null() {
                return Err(RacpError::new("MACHINE_SETUP_OWNERSHIP_CONFLICT"));
            }
            let mut length = 0;
            while length < 8192 && unsafe { *pointer.add(length) } != 0 {
                length += 1;
            }
            if length == 8192 {
                return Err(RacpError::new("MACHINE_SETUP_OWNERSHIP_CONFLICT"));
            }
            String::from_utf16(unsafe { std::slice::from_raw_parts(pointer, length) })
                .map_err(|_| RacpError::new("MACHINE_SETUP_OWNERSHIP_CONFLICT"))
        };
        let expected = format!(
            "\"{}\" native-setup-host --setup-root \"{}\" --input-sha256 {}",
            exe.to_string_lossy(),
            root.to_string_lossy(),
            hash
        );
        if config.dwServiceType != SERVICE_WIN32_OWN_PROCESS
            || config.dwStartType != SERVICE_DEMAND_START
            || !text(config.lpServiceStartName)?.eq_ignore_ascii_case("LocalSystem")
            || text(config.lpBinaryPathName)? != expected
        {
            return Err(RacpError::new("MACHINE_SETUP_OWNERSHIP_CONFLICT"));
        }
        let observed = status(&service)?;
        if observed.dwCurrentState != SERVICE_STOPPED || observed.dwProcessId != 0 {
            return Err(RacpError::new("MACHINE_SETUP_NOT_STOPPED"));
        }
        if unsafe { DeleteService(service.0) } == 0 {
            return Err(RacpError::new("MACHINE_SETUP_CLEANUP_UNKNOWN"));
        }
        true
    };
    let request_value: Value = serde_json::from_slice(&request)?;
    let result = json!({"format_version":1,"request_id":nonce,"input_sha256":hash,"helper_removal_requested":delete_requested,
        "target_service_preserved":true,"native_state_preserved":true,"action":request_value["type"].as_str().unwrap_or("provision")});
    racp_core::atomic_write(
        &root.join("cleanup.json"),
        &serde_json::to_vec(&result)?,
        true,
    )?;
    directory.unlink("request.json", false)?;
    Ok(result)
}
struct Context {
    root: PathBuf,
    hash: String,
}
static CONTEXT: OnceLock<Context> = OnceLock::new();
pub fn host(root: PathBuf, hash: String) -> Result<(), RacpError> {
    let parent = setup_parent()?;
    let nonce = root
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("");
    if root.parent() != Some(parent.as_path())
        || !nonce.strip_prefix("setup_").is_some_and(|id| {
            id.len() == 32
                && id
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        || hash.len() != 64
        || !hash
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    CONTEXT
        .set(Context { root, hash })
        .map_err(|_| RacpError::new("RESOURCE_BUSY"))?;
    let mut name = wide(NAME);
    let table = [
        SERVICE_TABLE_ENTRYW {
            lpServiceName: name.as_mut_ptr(),
            lpServiceProc: Some(main),
        },
        SERVICE_TABLE_ENTRYW {
            lpServiceName: std::ptr::null_mut(),
            lpServiceProc: None,
        },
    ];
    if unsafe { StartServiceCtrlDispatcherW(table.as_ptr()) } == 0 {
        return Err(RacpError::new("MACHINE_SETUP_HOST_UNAVAILABLE"));
    }
    Ok(())
}
unsafe extern "system" fn control(
    code: u32,
    _: u32,
    _: *mut core::ffi::c_void,
    _: *mut core::ffi::c_void,
) -> u32 {
    if code == SERVICE_CONTROL_INTERROGATE {
        ERROR_SUCCESS
    } else {
        ERROR_CALL_NOT_IMPLEMENTED
    }
}
fn report(handle: SERVICE_STATUS_HANDLE, state: u32, error: u32) {
    let status = SERVICE_STATUS {
        dwServiceType: SERVICE_WIN32_OWN_PROCESS,
        dwCurrentState: state,
        dwControlsAccepted: 0,
        dwWin32ExitCode: if error == 0 {
            0
        } else {
            ERROR_SERVICE_SPECIFIC_ERROR
        },
        dwServiceSpecificExitCode: error,
        dwCheckPoint: if state == SERVICE_START_PENDING { 1 } else { 0 },
        dwWaitHint: if state == SERVICE_START_PENDING {
            30000
        } else {
            0
        },
    };
    unsafe {
        SetServiceStatus(handle, &status);
    }
}
unsafe extern "system" fn main(count: u32, _: *mut *mut u16) {
    let handle =
        RegisterServiceCtrlHandlerExW(wide(NAME).as_ptr(), Some(control), std::ptr::null_mut());
    if handle.is_null() {
        return;
    }
    report(handle, SERVICE_START_PENDING, 0);
    let result = (|| -> Result<(), RacpError> {
        if count > 1 {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        machine::require_system()?;
        let _payload = racp_core::distribution::pin_current_agent_service_payload()?;
        let exe = std::env::current_exe()?;
        let _image = machine::pin_image_directory(
            exe.parent()
                .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?,
        )?;
        let context = CONTEXT
            .get()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        let _root = machine::pin_secure_parent(&context.root)?;
        let request = input(&context.root, &context.hash)?;
        validate_request(&request)?;
        report(handle, SERVICE_RUNNING, 0);
        let value: Value = serde_json::from_slice(&request)?;
        let maintenance = value["type"].as_str();
        let state = machine::data_parent()?.join("state");
        let result = if maintenance.is_some() {
            super::super::service::service_admin::execute(&state, &request)
        } else {
            super::super::service::service_provision::execute(&state, &request)
        };
        let receipt = match &result {
            Ok(value) => {
                json!({"format_version":1,"request_id":context.root.file_name().unwrap().to_string_lossy(),
                "input_sha256":context.hash,"complete":true,"prepared":maintenance.is_none(),"action":maintenance.unwrap_or("provision"),
                "agent_run_started":false,"device_registered":false,"result":value})
            }
            Err(error) => {
                json!({"format_version":1,"request_id":context.root.file_name().unwrap().to_string_lossy(),
                "input_sha256":context.hash,"complete":false,"prepared":false,"error_code":error.code.0})
            }
        };
        racp_core::atomic_write(
            &context.root.join("result.json"),
            &serde_json::to_vec(&receipt)?,
            false,
        )?;
        result.map(|_| ())
    })();
    report(handle, SERVICE_STOPPED, if result.is_ok() { 0 } else { 4 });
}
