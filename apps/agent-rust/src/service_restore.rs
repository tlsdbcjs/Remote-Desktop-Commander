//! Explicit native-to-native SCM restore; never imports state or consumes an enrollment token.
use racp_contract::{
    digest, local_ipc::reject_duplicate_keys, new_id, service_scope::*, RacpError,
};
use racp_core::{machine_service as machine, native_service::*, state_schema::*};
use serde_json::{json, Value};
use std::{
    path::Path,
    time::{Duration, Instant},
};
use windows_sys::Win32::{Foundation::*, System::Services::*};
struct Service(SC_HANDLE);
impl Drop for Service {
    fn drop(&mut self) {
        unsafe {
            CloseServiceHandle(self.0);
        }
    }
}
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
fn hash(value: &Value) -> Option<&str> {
    value.as_str().filter(|value| {
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}
fn status(service: &Service) -> Result<SERVICE_STATUS_PROCESS, RacpError> {
    let mut value = SERVICE_STATUS_PROCESS::default();
    let mut needed = 0;
    if unsafe {
        QueryServiceStatusEx(
            service.0,
            SC_STATUS_PROCESS_INFO,
            (&mut value as *mut SERVICE_STATUS_PROCESS).cast(),
            std::mem::size_of_val(&value) as u32,
            &mut needed,
        )
    } == 0
    {
        return Err(RacpError::new("SERVICE_RESTORE_STATUS_UNKNOWN"));
    }
    Ok(value)
}
fn start_type(service: &Service, kind: u32) -> Result<(), RacpError> {
    if unsafe {
        ChangeServiceConfigW(
            service.0,
            SERVICE_NO_CHANGE,
            kind,
            SERVICE_NO_CHANGE,
            std::ptr::null(),
            std::ptr::null(),
            std::ptr::null_mut(),
            std::ptr::null(),
            std::ptr::null(),
            std::ptr::null(),
            std::ptr::null(),
        )
    } == 0
    {
        return Err(RacpError::new("SERVICE_RESTORE_OUTCOME_UNKNOWN"));
    }
    Ok(())
}
fn verify(service: &Service, image: &str) -> Result<(), RacpError> {
    let mut words = vec![0usize; 16384 / std::mem::size_of::<usize>()];
    let begin = words.as_ptr() as usize;
    let end = begin + 16384;
    let raw = words.as_mut_ptr().cast::<QUERY_SERVICE_CONFIGW>();
    let mut needed = 0;
    if unsafe { QueryServiceConfigW(service.0, raw, 16384, &mut needed) } == 0 || needed > 16384 {
        return Err(RacpError::new("SERVICE_RESTORE_OUTCOME_UNKNOWN"));
    }
    let text = |pointer: *const u16| -> Result<String, RacpError> {
        let pointer_value = pointer as usize;
        if pointer_value < begin || pointer_value >= end || pointer_value % 2 != 0 {
            return Err(RacpError::new("SERVICE_RESTORE_OUTCOME_UNKNOWN"));
        }
        let data = unsafe { std::slice::from_raw_parts(pointer, (end - pointer_value) / 2) };
        let length = data
            .iter()
            .position(|character| *character == 0)
            .ok_or_else(|| RacpError::new("SERVICE_RESTORE_OUTCOME_UNKNOWN"))?;
        String::from_utf16(&data[..length])
            .map_err(|_| RacpError::new("SERVICE_RESTORE_OUTCOME_UNKNOWN"))
    };
    let value = unsafe { &*raw };
    let mut sid = SERVICE_SID_INFO::default();
    if value.dwServiceType != SERVICE_WIN32_OWN_PROCESS
        || value.dwStartType != SERVICE_DEMAND_START
        || text(value.lpBinaryPathName)? != image
        || !text(value.lpServiceStartName)?.eq_ignore_ascii_case(SERVICE_ACCOUNT)
        || unsafe {
            QueryServiceConfig2W(
                service.0,
                SERVICE_CONFIG_SERVICE_SID_INFO,
                (&mut sid as *mut SERVICE_SID_INFO).cast(),
                std::mem::size_of_val(&sid) as u32,
                &mut needed,
            )
        } == 0
        || sid.dwServiceSidType != SERVICE_SID_TYPE_UNRESTRICTED
    {
        return Err(RacpError::new("SERVICE_RESTORE_OUTCOME_UNKNOWN"));
    }
    let state = status(service)?;
    if state.dwCurrentState != SERVICE_STOPPED || state.dwProcessId != 0 {
        return Err(RacpError::new("SERVICE_RESTORE_NOT_STOPPED"));
    }
    Ok(())
}
pub fn execute(root: &Path, input: &Value) -> Result<Value, RacpError> {
    machine::require_system()?;
    let object = input
        .as_object()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    if object.len() != 4
        || object.keys().any(|key| {
            ![
                "type",
                "expected_config_sha256",
                "expected_device_id",
                "expected_bootstrap_sha256",
            ]
            .contains(&key.as_str())
        })
        || input["type"] != "restore_service"
        || hash(&input["expected_config_sha256"]).is_none()
        || !(input["expected_device_id"].is_null() || input["expected_device_id"].is_string())
        || !(input["expected_bootstrap_sha256"].is_null()
            || hash(&input["expected_bootstrap_sha256"]).is_some())
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let expected = input["expected_config_sha256"].as_str().unwrap();
    let _payload = racp_core::distribution::pin_current_payload(true)?;
    let exe = racp_core::validate_local_path(&std::env::current_exe()?)?;
    let image_root = exe
        .parent()
        .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
    let _image = machine::pin_image_directory(image_root)?;
    let config = NativeServiceConfig::read_restore(root, expected)?;
    let _private = machine::pin_private_sid(root, &config.scope.service_sid)?;
    racp_core::native_release::assert_compatible(root)?;
    let _config_lock = racp_core::InstanceLock::acquire(&root.join(CONFIG_LOCK))?;
    let _agent_lock = racp_core::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    if root.join("native-registration.pending").try_exists()?
        || root.join(CONFIG_PENDING).try_exists()?
    {
        return Err(RacpError::new("SERVICE_RESTORE_CONFLICT"));
    }
    let registered = root.join(STATE_FILE).try_exists()?;
    let bootstrap = if registered {
        let bytes = racp_core::read_bounded(&root.join(STATE_FILE), 65536, false)?;
        reject_duplicate_keys(&bytes)?;
        let state: NativeState = serde_json::from_slice(&bytes)?;
        if state.schema_version != STATE_SCHEMA_VERSION
            || input["expected_device_id"] != state.device_id
            || !input["expected_bootstrap_sha256"].is_null()
            || !root.join(SECRET_FILE).try_exists()?
        {
            return Err(RacpError::new("SERVICE_RESTORE_IDENTITY_CHANGED"));
        }
        None
    } else {
        if !input["expected_device_id"].is_null() || root.join(SECRET_FILE).try_exists()? {
            return Err(RacpError::new("SERVICE_RESTORE_CONFLICT"));
        }
        let bootstrap = hash(&input["expected_bootstrap_sha256"])
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        if digest(racp_core::read_bounded(
            &root.join(BOOTSTRAP_FILE),
            65536,
            false,
        )?) != bootstrap
        {
            return Err(RacpError::new("SERVICE_RESTORE_INPUT_CHANGED"));
        }
        Some(bootstrap)
    };
    let quote = |path: &Path| -> Result<String, RacpError> {
        let value = path.to_string_lossy();
        if value.contains(['"', '\0', '\r', '\n']) || value.ends_with('\\') {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        Ok(format!("\"{value}\""))
    };
    let mut image = format!(
        "{} native-service --state-dir {}",
        quote(&exe)?,
        quote(root)?
    );
    if let Some(bootstrap) = bootstrap {
        image.push_str(&format!(" --bootstrap-sha256 {bootstrap}"));
    }
    if image.encode_utf16().count() > 8192 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let manager = unsafe {
        OpenSCManagerW(
            std::ptr::null(),
            std::ptr::null(),
            SC_MANAGER_CONNECT | SC_MANAGER_CREATE_SERVICE,
        )
    };
    if manager.is_null() {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let manager = Service(manager);
    let old = unsafe { OpenServiceW(manager.0, wide(SERVICE_NAME).as_ptr(), SERVICE_QUERY_STATUS) };
    if !old.is_null() {
        drop(Service(old));
        return Err(RacpError::new("SERVICE_RESTORE_ALREADY_CONFIGURED"));
    }
    if unsafe { GetLastError() } != ERROR_SERVICE_DOES_NOT_EXIST {
        return Err(RacpError::new("SERVICE_RESTORE_STATUS_UNKNOWN"));
    }
    let nonce = new_id("restore");
    let write = |phase: &str| {
        racp_core::atomic_write(
            &root.join("native-service-restore.json"),
            &serde_json::to_vec(&json!({"format_version":1,
        "request_id":nonce,"phase":phase,"config_sha256":expected,"device_id":input["expected_device_id"],"state_preserved":true,
        "payload_manifest_sha256":digest(racp_core::read_bounded(&image_root.join(racp_contract::distribution::MANIFEST_FILE),1048576,false)?)}))?,
            true,
        )
    };
    write("reserved")?;
    let service = unsafe {
        CreateServiceW(
            manager.0,
            wide(SERVICE_NAME).as_ptr(),
            wide("RACP Native Agent").as_ptr(),
            SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | SERVICE_CHANGE_CONFIG | SERVICE_START,
            SERVICE_WIN32_OWN_PROCESS,
            SERVICE_DISABLED,
            SERVICE_ERROR_NORMAL,
            wide(&image).as_ptr(),
            std::ptr::null(),
            std::ptr::null_mut(),
            std::ptr::null(),
            wide(SERVICE_ACCOUNT).as_ptr(),
            std::ptr::null(),
        )
    };
    if service.is_null() {
        return Err(RacpError::new("SERVICE_RESTORE_OUTCOME_UNKNOWN"));
    }
    let service = Service(service);
    write("created_disabled")?;
    let sid = SERVICE_SID_INFO {
        dwServiceSidType: SERVICE_SID_TYPE_UNRESTRICTED,
    };
    if unsafe {
        ChangeServiceConfig2W(
            service.0,
            SERVICE_CONFIG_SERVICE_SID_INFO,
            (&sid as *const SERVICE_SID_INFO).cast(),
        )
    } == 0
        || racp_core::service_directory::expected_service_sid()? != config.scope.service_sid
    {
        return Err(RacpError::new("SERVICE_RESTORE_IDENTITY_CHANGED"));
    }
    machine::grant_parent_read(
        root.parent()
            .ok_or_else(|| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))?,
        &config.scope,
    )?;
    drop(_agent_lock);
    drop(_config_lock);
    start_type(&service, SERVICE_DEMAND_START)?;
    write("maintenance_requested")?;
    let request = new_id("maintenance");
    let args = [wide("--maintenance"), wide(&request)];
    let pointers: Vec<_> = args.iter().map(|arg| arg.as_ptr()).collect();
    if unsafe { StartServiceW(service.0, 2, pointers.as_ptr()) } == 0 {
        let _ = start_type(&service, SERVICE_DISABLED);
        return Err(RacpError::new("SERVICE_RESTORE_VALIDATION_UNKNOWN"));
    }
    let deadline = Instant::now() + Duration::from_secs(60);
    let validated = (|| -> Result<(), RacpError> {
        loop {
            let state = status(&service)?;
            if state.dwCurrentState == SERVICE_STOPPED && state.dwProcessId == 0 {
                if let Ok(bytes) = racp_core::read_bounded(
                    &root.join(racp_runtime::service_maintenance::RESULT_FILE),
                    4096,
                    false,
                ) {
                    reject_duplicate_keys(&bytes)?;
                    let value: Value = serde_json::from_slice(&bytes)?;
                    if value["request_id"] == request {
                        if state.dwWin32ExitCode != 0
                            || value["format_version"] != 1
                            || value["preflight_complete"] != true
                            || value["config_sha256"] != expected
                            || value["device_id"] != input["expected_device_id"]
                        {
                            return Err(RacpError::new("SERVICE_RESTORE_VALIDATION_UNKNOWN"));
                        }
                        break;
                    }
                }
            }
            if Instant::now() >= deadline {
                return Err(RacpError::new("SERVICE_RESTORE_VALIDATION_UNKNOWN"));
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        Ok(())
    })();
    if let Err(error) = validated {
        if status(&service)
            .is_ok_and(|state| state.dwCurrentState == SERVICE_STOPPED && state.dwProcessId == 0)
        {
            let _ = start_type(&service, SERVICE_DISABLED);
        }
        write("validation_unconfirmed")?;
        return Err(error);
    }
    verify(&service, &image)?;
    let _final_config = racp_core::InstanceLock::acquire(&root.join(CONFIG_LOCK))?;
    let _final_agent = racp_core::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    if digest(racp_core::read_bounded(
        &root.join(CONFIG_FILE),
        16384,
        false,
    )?) != expected
    {
        let _ = start_type(&service, SERVICE_DISABLED);
        return Err(RacpError::new("SERVICE_RESTORE_IDENTITY_CHANGED"));
    }
    if registered {
        racp_core::native_release::publish(root)?;
    }
    write("restored_demand_start")?;
    Ok(
        json!({"restored":true,"agent_run_started":false,"registered":registered,"state_preserved":true,
        "credential_accessed":false,"device_id":input["expected_device_id"],"request_id":nonce}),
    )
}
