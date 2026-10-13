//! Explicit native-to-native SCM restore; never imports state or consumes an enrollment token.
use racp_contract::service_start::{MaintenancePurpose, StartCommand, StartPhase};
use racp_contract::{
    digest, local_ipc::reject_duplicate_keys, new_id, service_scope::*, RacpError,
};
use racp_core::service_start::StartStore;
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
fn verify(service: &Service, image: &str, allow_disabled: bool) -> Result<(), RacpError> {
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
        || !(value.dwStartType == SERVICE_DEMAND_START
            || allow_disabled && value.dwStartType == SERVICE_DISABLED)
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
    if ![4, 6].contains(&object.len())
        || object.keys().any(|key| {
            ![
                "type",
                "expected_config_sha256",
                "expected_device_id",
                "expected_bootstrap_sha256",
                "resume_request_id",
                "resume_expected_sha256",
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
    let cas = super::start_intent::resume_cas(input)?;
    let _payload = racp_core::distribution::pin_current_agent_service_payload()?;
    let exe = racp_core::validate_local_path(&std::env::current_exe()?)?;
    let image_root = exe
        .parent()
        .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
    let _image = machine::pin_image_directory(image_root)?;
    let config = NativeServiceConfig::read_restore(root, expected)?;
    let store = StartStore::new(machine::pin_private_sid(root, &config.scope.service_sid)?)?;
    let _administration = store.admin_guard()?;
    let execution = store.execution_guard()?;
    let prior_intent = store.snapshot()?;
    let restoring = prior_intent.as_ref().is_some_and(|record| {
        matches!(
            &record.intent.command,
            StartCommand::Maintenance {
                purpose: MaintenancePurpose::Restore,
                ..
            }
        ) && !record.intent.permits_normal_run()
    });
    let resumed_intent = if restoring {
        super::start_intent::resume_maintenance(
            prior_intent.as_ref(),
            MaintenancePurpose::Restore,
            cas,
        )?;
        let record = prior_intent.as_ref().unwrap();
        super::start_intent::maintenance_receipt(
            root,
            &store,
            record,
            expected,
            &input["expected_device_id"],
        )?;
        Some(record.clone())
    } else {
        if cas.is_some() {
            return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
        }
        None
    };
    let removed_intent = match prior_intent.filter(|_| !restoring) {
        Some(record) if !record.intent.permits_normal_run() => {
            if !matches!(
                &record.intent.command,
                StartCommand::Maintenance {
                    purpose: MaintenancePurpose::Remove,
                    ..
                }
            ) || !matches!(&record.intent.phase, StartPhase::Completed { .. })
            {
                return Err(RacpError::new("SERVICE_START_BLOCKED"));
            }
            let raw = super::start_intent::receipt_bytes(root, &record.intent.command)?;
            reject_duplicate_keys(&raw)?;
            let receipt: Value = serde_json::from_slice(&raw)?;
            let StartCommand::Maintenance { request_id, .. } = &record.intent.command else {
                unreachable!()
            };
            if receipt["request_id"] != *request_id
                || receipt["preflight_complete"] != true
                || receipt["format_version"] != 1
                || receipt["config_sha256"] != expected
                || receipt["device_id"] != input["expected_device_id"]
                || super::start_intent::completed(
                    root,
                    &store,
                    &record.intent.request_id,
                    &record.intent.command,
                )?
                .is_none()
            {
                return Err(RacpError::new("SERVICE_START_RECOVERY_REQUIRED"));
            }
            Some(record)
        }
        _ => None,
    };
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
    if let Some(record) = resumed_intent {
        let manager =
            unsafe { OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_CONNECT) };
        if manager.is_null() {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let manager = Service(manager);
        let service = unsafe {
            OpenServiceW(
                manager.0,
                wide(SERVICE_NAME).as_ptr(),
                SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | SERVICE_CHANGE_CONFIG,
            )
        };
        if service.is_null() {
            return Err(RacpError::new("SERVICE_RESTORE_STATUS_UNKNOWN"));
        }
        let service = Service(service);
        verify(&service, &image, true)?;
        super::recovery::verify(service.0)?;
        drop(_agent_lock);
        drop(_config_lock);
        drop(execution);
        return finalize(
            root, input, &store, &record, &service, &image, image_root, registered,
        );
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
    // Only confirmed namespace absence lets restore consume a completed removal barrier.
    if let Some(record) = removed_intent {
        store.acknowledge(
            &record.intent.request_id,
            &record.sha256,
            &super::start_intent::receipt_bytes(root, &record.intent.command)?,
        )?;
    }
    let request = new_id("maintenance");
    let command = StartCommand::Maintenance {
        purpose: MaintenancePurpose::Restore,
        request_id: request.clone(),
    };
    let intent = store.prepare(command.clone())?;
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
    // A newly created disabled definition has no actions yet. Establish NONE
    // before its one-shot start; finalization and resumption require readback.
    super::recovery::configure(service.0)?;
    machine::grant_parent_read(
        root.parent()
            .ok_or_else(|| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))?,
        &config.scope,
    )?;
    drop(_agent_lock);
    drop(_config_lock);
    drop(execution);
    start_type(&service, SERVICE_DEMAND_START)?;
    write("maintenance_requested")?;
    let args = [wide("--admin-intent"), wide(&intent.intent.request_id)];
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
                        if super::start_intent::completed(
                            root,
                            &store,
                            &intent.intent.request_id,
                            &command,
                        )?
                        .is_some()
                        {
                            break;
                        }
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
    let completed =
        super::start_intent::completed(root, &store, &intent.intent.request_id, &command)?
            .ok_or_else(|| RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"))?;
    finalize(
        root, input, &store, &completed, &service, &image, image_root, registered,
    )
}

fn finalize(
    root: &Path,
    input: &Value,
    store: &StartStore,
    record: &racp_core::service_start::StartSnapshot,
    service: &Service,
    image: &str,
    image_root: &Path,
    registered: bool,
) -> Result<Value, RacpError> {
    let expected = input["expected_config_sha256"].as_str().unwrap();
    let nonce = new_id("restore");
    let _execution = store.execution_guard()?;
    let _final_config = racp_core::InstanceLock::acquire(&root.join(CONFIG_LOCK))?;
    let _final_agent = racp_core::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    verify(service, image, true)?;
    if status(service)?.dwWin32ExitCode != 0 {
        return Err(RacpError::new("SERVICE_RESTORE_VALIDATION_UNKNOWN"));
    }
    let completed = super::start_intent::completed(
        root,
        store,
        &record.intent.request_id,
        &record.intent.command,
    )?
    .filter(|current| current.sha256 == record.sha256)
    .ok_or_else(|| RacpError::new("SERVICE_START_INTENT_CONFLICT"))?;
    super::start_intent::maintenance_receipt(
        root,
        store,
        &completed,
        expected,
        &input["expected_device_id"],
    )?;
    if digest(racp_core::read_bounded(
        &root.join(CONFIG_FILE),
        16384,
        false,
    )?) != expected
    {
        let _ = start_type(service, SERVICE_DISABLED);
        return Err(RacpError::new("SERVICE_RESTORE_IDENTITY_CHANGED"));
    }
    if root.join("native-registration.pending").try_exists()?
        || root.join(CONFIG_PENDING).try_exists()?
        || root.join(STATE_FILE).try_exists()? != registered
    {
        return Err(RacpError::new("SERVICE_RESTORE_CONFLICT"));
    }
    if registered {
        let bytes = racp_core::read_bounded(&root.join(STATE_FILE), 65536, false)?;
        reject_duplicate_keys(&bytes)?;
        let state: NativeState = serde_json::from_slice(&bytes)?;
        if state.schema_version != STATE_SCHEMA_VERSION
            || input["expected_device_id"] != state.device_id
            || !root.join(SECRET_FILE).try_exists()?
        {
            return Err(RacpError::new("SERVICE_RESTORE_IDENTITY_CHANGED"));
        }
    } else if root.join(SECRET_FILE).try_exists()?
        || digest(racp_core::read_bounded(
            &root.join(BOOTSTRAP_FILE),
            65536,
            false,
        )?) != input["expected_bootstrap_sha256"]
    {
        return Err(RacpError::new("SERVICE_RESTORE_INPUT_CHANGED"));
    }
    // Maintenance is one-shot: explicitly keep recovery disabled after its confirmed stop.
    super::recovery::verify(service.0)?;
    if let Err(error) = super::recovery::configure(service.0) {
        let _ = start_type(service, SERVICE_DISABLED);
        return Err(error);
    }
    start_type(service, SERVICE_DEMAND_START)?;
    verify(service, image, false)?;
    if registered {
        racp_core::native_release::publish(root)?;
    }
    racp_core::atomic_write(
        &root.join("native-service-restore.json"),
        &serde_json::to_vec(&json!({
            "format_version":1,"request_id":nonce,"phase":"restored_demand_start","config_sha256":expected,
            "device_id":input["expected_device_id"],"state_preserved":true,
            "payload_manifest_sha256":digest(racp_core::read_bounded(&image_root.join(racp_contract::distribution::MANIFEST_FILE),1048576,false)?)
        }))?,
        true,
    )?;
    store.acknowledge(
        &record.intent.request_id,
        &completed.sha256,
        &super::start_intent::receipt_bytes(root, &record.intent.command)?,
    )?;
    Ok(
        json!({"restored":true,"agent_run_started":false,"registered":registered,"state_preserved":true,
        "credential_accessed":false,"device_id":input["expected_device_id"],"request_id":nonce,
        "start_intent_id":record.intent.request_id,
        "startup_policy":"demand", "crash_recovery":racp_contract::service_recovery::ServiceRecoveryPolicy::default()}),
    )
}
