//! Fresh SYSTEM provisioning only. No existing service/root is adopted or reconfigured.
use racp_contract::{digest, local_ipc::reject_duplicate_keys, service_scope::*, RacpError};
use racp_core::{machine_service as machine, native_service::*, state_schema::*};
use serde_json::{json, Value};
use std::{io::Read, path::Path};
use windows_sys::Win32::{Foundation::*, System::Services::*};

struct Handle(SC_HANDLE);
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            CloseServiceHandle(self.0);
        }
    }
}
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}
fn verify_created(service: &Handle, command: &str) -> Result<(), RacpError> {
    let mut buffer = vec![0usize; 16384 / std::mem::size_of::<usize>()];
    let begin = buffer.as_ptr() as usize;
    let end = begin + 16384;
    let raw = buffer.as_mut_ptr().cast::<QUERY_SERVICE_CONFIGW>();
    let mut needed = 0;
    if unsafe { QueryServiceConfigW(service.0, raw, 16384, &mut needed) } == 0 || needed > 16384 {
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    let text = |ptr: *const u16| -> Result<String, RacpError> {
        let address = ptr as usize;
        if address < begin || address >= end || address % 2 != 0 {
            return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
        }
        let length = (end - address) / 2;
        let chars = unsafe { std::slice::from_raw_parts(ptr, length) };
        let length = chars
            .iter()
            .position(|value| *value == 0)
            .ok_or_else(|| RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"))?;
        String::from_utf16(&chars[..length])
            .map_err(|_| RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"))
    };
    let config = unsafe { &*raw };
    if config.dwServiceType != SERVICE_WIN32_OWN_PROCESS
        || config.dwStartType != SERVICE_DEMAND_START
        || text(config.lpBinaryPathName)? != command
        || text(config.lpServiceStartName)?.to_ascii_lowercase()
            != SERVICE_ACCOUNT.to_ascii_lowercase()
    {
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    let mut sid = SERVICE_SID_INFO::default();
    if unsafe {
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
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    let mut status = SERVICE_STATUS_PROCESS::default();
    if unsafe {
        QueryServiceStatusEx(
            service.0,
            SC_STATUS_PROCESS_INFO,
            (&mut status as *mut SERVICE_STATUS_PROCESS).cast(),
            std::mem::size_of_val(&status) as u32,
            &mut needed,
        )
    } == 0
        || status.dwCurrentState != SERVICE_STOPPED
        || status.dwProcessId != 0
    {
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    Ok(())
}
fn quote(path: &Path) -> Result<String, RacpError> {
    let text = path
        .to_str()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    if text.contains(['"', '\0', '\r', '\n']) || text.ends_with('\\') {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    Ok(format!("\"{text}\""))
}
fn progress(
    parent: &Path,
    nonce: &str,
    phase: &str,
    payload_hash: &str,
    bootstrap_hash: &str,
) -> Result<(), RacpError> {
    racp_core::atomic_write(
        &parent.join("native-service-provision.json"),
        &serde_json::to_vec(&json!({"format_version":1,"nonce":nonce,"phase":phase,
            "service_name":SERVICE_NAME,"payload_manifest_sha256":payload_hash,
            "bootstrap_sha256":bootstrap_hash,"registered":false,"ready":false}))?,
        true,
    )
    .map_err(|_| RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"))
}
pub fn execute(root: &Path, raw: &[u8]) -> Result<Value, RacpError> {
    machine::require_system()?;
    let _payload = racp_core::distribution::pin_current_agent_service_payload()?;
    let exe = racp_core::validate_local_path(&std::env::current_exe()?)?;
    if exe.file_name().is_none_or(|name| name != "racp-agent.exe") {
        return Err(RacpError::new("PACKAGE_INVALID"));
    }
    let image = exe
        .parent()
        .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
    let _image = machine::pin_image_directory(image)?;
    let parent = machine::data_parent()?;
    let state = parent.join("state");
    let control = parent.join("control");
    let same = |a: &Path, b: &Path| racp_core::path_within(a, b) && racp_core::path_within(b, a);
    if !same(&racp_core::validate_local_path(root)?, &state) || parent.try_exists()? {
        return Err(RacpError::new("SERVICE_PROVISION_CONFLICT"));
    }
    if raw.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    reject_duplicate_keys(raw)?;
    let input: Value = serde_json::from_slice(raw)?;
    if input.as_object().is_none_or(|object| {
        object.len() != 2
            || object
                .keys()
                .any(|key| !["controllers", "enrollment"].contains(&key.as_str()))
    }) {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let controllers: Vec<ServiceController> = serde_json::from_value(input["controllers"].clone())?;
    // Validate SID/session/role grammar before any mutation; the real fixed service SID is read below.
    ServiceScope {
        service_name: SERVICE_NAME.into(),
        service_sid: "S-1-5-80-0-0-0-0-0".into(),
        controllers: controllers.clone(),
    }
    .validate()?;
    let enrollment: racp_runtime::native_client::Enrollment =
        serde_json::from_value(input["enrollment"].clone())?;
    if enrollment.policy.revision != 0 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let (connection_file, _) = racp_core::load_connection(
        &enrollment.connection_file,
        Some(&enrollment.connection_sha256.to_ascii_lowercase()),
    )?;
    let workspace_state = NativeState {
        schema_version: STATE_SCHEMA_VERSION,
        settings_revision: 0,
        gateway: connection_file.gateway,
        device_id: "dev_pending".into(),
        workspace: enrollment.workspace.clone(),
        allowed_workspaces: enrollment.allowed_workspaces.clone(),
        profile: enrollment.profile.clone(),
        ca_file: None,
    };
    workspace_state.agent_settings(&state, &enrollment.policy)?;
    let source_parent = enrollment
        .connection_file
        .parent()
        .ok_or_else(|| RacpError::new("CONNECTION_FILE_INVALID"))?;
    let source_guard = racp_core::Workspaces::new(source_parent, &[])?;
    let source = source_guard.directory("default", source_parent)?;
    let mut pin = source.open_pinned_read(
        enrollment
            .connection_file
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| RacpError::new("CONNECTION_FILE_INVALID"))?,
    )?;
    let mut connection = vec![];
    (&mut pin).take(65537).read_to_end(&mut connection)?;
    if connection.len() > 65536
        || digest(&connection) != enrollment.connection_sha256.to_ascii_lowercase()
    {
        return Err(RacpError::new("CONNECTION_FILE_CHANGED"));
    }
    let bootstrap = serde_json::to_vec(
        &json!({"connection_file":state.join(CONNECTION_FILE),"connection_sha256":digest(&connection),
        "workspace":enrollment.workspace,"allowed_workspaces":enrollment.allowed_workspaces,"profile":enrollment.profile,"policy":enrollment.policy}),
    )?;
    if bootstrap.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let bootstrap_hash = digest(&bootstrap);
    let payload_hash = digest(racp_core::read_bounded(
        &image.join(racp_contract::distribution::MANIFEST_FILE),
        1024 * 1024,
        false,
    )?);
    let command = format!(
        "{} native-service --state-dir {} --bootstrap-sha256 {}",
        quote(&exe)?,
        quote(&state)?,
        bootstrap_hash
    );
    if command.encode_utf16().count() > 8192 {
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
    let manager = Handle(manager);
    let existing =
        unsafe { OpenServiceW(manager.0, wide(SERVICE_NAME).as_ptr(), SERVICE_QUERY_STATUS) };
    if !existing.is_null() {
        drop(Handle(existing));
        return Err(RacpError::new("SERVICE_PROVISION_CONFLICT"));
    }
    if unsafe { GetLastError() } != ERROR_SERVICE_DOES_NOT_EXIST {
        return Err(RacpError::new("SERVICE_OWNERSHIP_CONFLICT"));
    }
    let _privilege = machine::RestorePrivilege::enable()?;
    let _parent = machine::create_system_parent(&parent)?;
    let nonce = racp_contract::new_id("provision");
    progress(&parent, &nonce, "reserved", &payload_hash, &bootstrap_hash)?;
    let service = unsafe {
        CreateServiceW(
            manager.0,
            wide(SERVICE_NAME).as_ptr(),
            wide("RACP Native Agent").as_ptr(),
            SERVICE_CHANGE_CONFIG | SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS,
            SERVICE_WIN32_OWN_PROCESS,
            SERVICE_DISABLED,
            SERVICE_ERROR_NORMAL,
            wide(&command).as_ptr(),
            std::ptr::null(),
            std::ptr::null_mut(),
            std::ptr::null(),
            wide(SERVICE_ACCOUNT).as_ptr(),
            std::ptr::null(),
        )
    };
    if service.is_null() {
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    let service = Handle(service);
    progress(
        &parent,
        &nonce,
        "service_created_disabled",
        &payload_hash,
        &bootstrap_hash,
    )?;
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
    {
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    let scope = ServiceScope {
        service_name: SERVICE_NAME.into(),
        service_sid: racp_core::service_directory::expected_service_sid()?,
        controllers,
    };
    scope.validate()?;
    let config = NativeServiceConfig::initial(state.clone(), control.clone(), scope.clone())?;
    let _private = machine::create_private(&state)?;
    let _control = racp_core::service_directory::create(&control, &scope)?;
    let config_raw = serde_json::to_vec(&config)?;
    if config_raw.len() > 16384 {
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    for (name, bytes) in [
        (CONFIG_FILE, config_raw.as_slice()),
        (CONNECTION_FILE, connection.as_slice()),
        (BOOTSTRAP_FILE, bootstrap.as_slice()),
    ] {
        racp_core::atomic_write(&state.join(name), bytes, false)
            .map_err(|_| RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"))?;
        if racp_core::read_bounded(&state.join(name), 65536, false)? != bytes {
            return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
        }
    }
    machine::grant_parent_read(&parent, &scope)?;
    progress(
        &parent,
        &nonce,
        "inputs_committed",
        &payload_hash,
        &bootstrap_hash,
    )?;
    super::recovery::configure(service.0)?;
    progress(
        &parent,
        &nonce,
        "recovery_committed",
        &payload_hash,
        &bootstrap_hash,
    )?;
    if unsafe {
        ChangeServiceConfigW(
            service.0,
            SERVICE_NO_CHANGE,
            SERVICE_DEMAND_START,
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
        return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
    }
    verify_created(&service, &command)?;
    progress(
        &parent,
        &nonce,
        "prepared_demand_start",
        &payload_hash,
        &bootstrap_hash,
    )?;
    Ok(
        json!({"prepared":true,"started":false,"registered":false,"ready":false,"service_name":SERVICE_NAME,
        "state_dir":state,"control_dir":control,"bootstrap_sha256":bootstrap_hash,"nonce":nonce,
        "recovery":"inspect_existing_disabled_or_demand_service_and_provision_receipt_no_automatic_adoption",
        "startup_policy":"demand", "crash_recovery":racp_contract::service_recovery::ServiceRecoveryPolicy::default()}),
    )
}
