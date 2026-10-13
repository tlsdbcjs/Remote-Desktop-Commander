//! Installer SYSTEM handoff only. No arbitrary service name/account/image or token argument.
use racp_contract::service_start::{MaintenancePurpose, StartCommand};
use racp_contract::{
    digest,
    local_ipc::reject_duplicate_keys,
    service_scope::{SERVICE_ACCOUNT, SERVICE_NAME},
    RacpError,
};
use racp_core::service_start::StartStore;
use serde_json::{json, Value};
use std::{
    ffi::c_void,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*, Storage::FileSystem::DELETE, System::Services::*, UI::Shell::CommandLineToArgvW,
};
struct Service(SC_HANDLE);
impl Drop for Service {
    fn drop(&mut self) {
        unsafe {
            CloseServiceHandle(self.0);
        }
    }
}
struct Local(*mut c_void);
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
unsafe fn text(ptr: *const u16, limit: usize) -> Result<String, RacpError> {
    if ptr.is_null() {
        return Err(RacpError::new("SERVICE_OWNERSHIP_CONFLICT"));
    }
    let mut length = 0;
    while length <= limit && *ptr.add(length) != 0 {
        length += 1;
    }
    if length > limit {
        return Err(RacpError::new("SERVICE_OWNERSHIP_CONFLICT"));
    }
    String::from_utf16(std::slice::from_raw_parts(ptr, length))
        .map_err(|_| RacpError::new("SERVICE_OWNERSHIP_CONFLICT"))
}
fn status(service: &Service) -> Result<SERVICE_STATUS_PROCESS, RacpError> {
    let mut result = SERVICE_STATUS_PROCESS::default();
    let mut needed = 0;
    if unsafe {
        QueryServiceStatusEx(
            service.0,
            SC_STATUS_PROCESS_INFO,
            (&mut result as *mut SERVICE_STATUS_PROCESS).cast(),
            std::mem::size_of_val(&result) as u32,
            &mut needed,
        )
    } == 0
    {
        return Err(RacpError::new("SERVICE_STATUS_UNAVAILABLE"));
    }
    Ok(result)
}
fn owned(root: &Path) -> Result<Service, RacpError> {
    owned_rights(root, SERVICE_START)
}
fn owned_rights(root: &Path, rights: u32) -> Result<Service, RacpError> {
    let manager = unsafe { OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_CONNECT) };
    if manager.is_null() {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let manager = Service(manager);
    let service = unsafe {
        OpenServiceW(
            manager.0,
            wide(SERVICE_NAME).as_ptr(),
            SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS | rights,
        )
    };
    if service.is_null() {
        return Err(RacpError::new("SERVICE_NOT_INSTALLED"));
    }
    let service = Service(service);
    let mut words = vec![0usize; 16384 / std::mem::size_of::<usize>()];
    let mut needed = 0;
    let raw = words.as_mut_ptr().cast::<QUERY_SERVICE_CONFIGW>();
    if unsafe { QueryServiceConfigW(service.0, raw, 16384, &mut needed) } == 0 || needed > 16384 {
        return Err(RacpError::new("SERVICE_OWNERSHIP_CONFLICT"));
    }
    let config = unsafe { &*raw };
    if config.dwServiceType != SERVICE_WIN32_OWN_PROCESS
        || unsafe { text(config.lpServiceStartName, 256)? }.to_ascii_lowercase()
            != SERVICE_ACCOUNT.to_ascii_lowercase()
    {
        return Err(RacpError::new("SERVICE_OWNERSHIP_CONFLICT"));
    }
    let binary = unsafe { text(config.lpBinaryPathName, 8192)? };
    let mut count = 0;
    let args = unsafe { CommandLineToArgvW(wide(&binary).as_ptr(), &mut count) };
    if args.is_null() || ![4, 6].contains(&count) {
        return Err(RacpError::new("SERVICE_OWNERSHIP_CONFLICT"));
    }
    let _args = Local(args.cast());
    let values = unsafe { std::slice::from_raw_parts(args, count as usize) }
        .iter()
        .map(|ptr| unsafe { text(*ptr, 8192) })
        .collect::<Result<Vec<_>, _>>()?;
    let exe = racp_core::validate_local_path(&std::env::current_exe()?)?;
    let registered = racp_core::validate_local_path(&PathBuf::from(&values[0]))?;
    let configured = racp_core::validate_local_path(&PathBuf::from(&values[3]))?;
    let same = |a: &Path, b: &Path| racp_core::path_within(a, b) && racp_core::path_within(b, a);
    if !same(&exe, &registered)
        || !same(root, &configured)
        || values[1] != "native-service"
        || values[2] != "--state-dir"
        || values.len() == 6
            && (values[4] != "--bootstrap-sha256"
                || values[5].len() != 64
                || !values[5].bytes().all(|v| v.is_ascii_hexdigit()))
    {
        return Err(RacpError::new("SERVICE_OWNERSHIP_CONFLICT"));
    }
    let observed = status(&service)?;
    if observed.dwCurrentState != SERVICE_STOPPED || observed.dwProcessId != 0 {
        return Err(RacpError::new("SERVICE_NOT_STOPPED"));
    }
    Ok(service)
}
pub fn execute(root: &Path, raw: &[u8]) -> Result<Value, RacpError> {
    racp_core::machine_service::require_system()?;
    let root = racp_core::validate_local_path(root)?;
    let _payload = racp_core::distribution::pin_current_agent_service_payload()?;
    let exe = std::env::current_exe()?;
    let parent = exe
        .parent()
        .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
    let _image = racp_core::machine_service::pin_image_directory(parent)?;
    reject_duplicate_keys(raw)?;
    let input: Value = serde_json::from_slice(raw)?;
    let object = input
        .as_object()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    let action = input["type"]
        .as_str()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    if action == "restore_service" {
        return super::service_restore::execute(&root, &input);
    }
    let store = StartStore::new(racp_core::machine_service::pin_private(&root)?)?;
    let _administration = store.admin_guard()?;
    let service = owned(&root)?;
    if let Some(result) = manage_intent(&root, &input, &store, &service)? {
        return Ok(result);
    }
    if action == "remove_service" {
        return remove_service(&root, &input, &store);
    }
    let execution = store.execution_guard()?;
    super::recovery::verify(service.0)?; // No adoption of externally enabled restart actions.
    store.allow_run()?;
    let (command, input_hash) = match action {
        "recover_sessions" => {
            if object.len() != 1 {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            let nonce = racp_contract::new_id("session_recovery");
            (
                StartCommand::RecoverSessions {
                    request_id: nonce.clone(),
                },
                Some(nonce),
            )
        }
        "configure" => {
            if object.len() != 4
                || object.keys().any(|key| {
                    ![
                        "type",
                        "source_file",
                        "source_sha256",
                        "expected_staged_sha256",
                    ]
                    .contains(&key.as_str())
                })
            {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            let path = input["source_file"]
                .as_str()
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
            let hash = input["source_sha256"]
                .as_str()
                .filter(|hash| hash.len() == 64 && hash.bytes().all(|v| v.is_ascii_hexdigit()))
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
            let bytes = racp_core::read_bounded(Path::new(path), 65536, false)?;
            if digest(&bytes) != hash.to_ascii_lowercase() {
                return Err(RacpError::new("SERVICE_CONFIG_INPUT_CHANGED"));
            }
            let prior = if input["expected_staged_sha256"].is_null() {
                None
            } else {
                Some(
                    input["expected_staged_sha256"]
                        .as_str()
                        .filter(|hash| {
                            hash.len() == 64 && hash.bytes().all(|v| v.is_ascii_hexdigit())
                        })
                        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?
                        .to_ascii_lowercase(),
                )
            };
            let hash = racp_core::service_config::stage_admin(&root, &bytes, prior.as_deref())?;
            (
                StartCommand::Configure {
                    input_sha256: hash.clone(),
                },
                Some(hash),
            )
        }
        "reconcile" => {
            if object.len() != 1 {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            (StartCommand::Reconcile {}, None)
        }
        _ => return Err(RacpError::new("REQUEST_INVALID")),
    };
    // No stop/reconfigure of a running or foreign service, even on a race after staging.
    let observed = status(&service)?;
    if observed.dwCurrentState != SERVICE_STOPPED || observed.dwProcessId != 0 {
        return Err(RacpError::new("SERVICE_NOT_STOPPED"));
    }
    let intent = store.prepare(command.clone())?;
    let arguments = [wide("--admin-intent"), wide(&intent.intent.request_id)];
    drop(execution); // The service actor holds the execution lock for its entire lifetime.
    let ptrs: Vec<_> = arguments.iter().map(|arg| arg.as_ptr()).collect();
    if unsafe { StartServiceW(service.0, ptrs.len() as u32, ptrs.as_ptr()) } == 0 {
        return Err(RacpError::new("SERVICE_CONFIG_START_UNKNOWN"));
    }
    let deadline = Instant::now() + Duration::from_secs(120);
    let mut began = false;
    loop {
        let observed = status(&service)?;
        if observed.dwCurrentState != SERVICE_STOPPED {
            began = true;
        }
        if observed.dwCurrentState == SERVICE_STOPPED && observed.dwProcessId == 0 && began {
            if observed.dwWin32ExitCode != 0 {
                return Err(RacpError::new(if action == "recover_sessions" {
                    "SERVICE_SESSION_RECOVERY_INCOMPLETE"
                } else {
                    "SERVICE_CONFIG_OUTCOME_UNKNOWN"
                }));
            }
            if super::start_intent::completed(&root, &store, &intent.intent.request_id, &command)?
                .is_some()
            {
                break;
            }
        }
        // A very fast maintenance run may already be stopped; the durable receipt
        // rather than START/STOP observations proves its committed result.
        if observed.dwCurrentState == SERVICE_STOPPED
            && observed.dwProcessId == 0
            && observed.dwWin32ExitCode == 0
            && super::start_intent::completed(&root, &store, &intent.intent.request_id, &command)?
                .is_some()
        {
            break;
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _execution = store.execution_guard()?;
    let observed = status(&service)?;
    if observed.dwCurrentState != SERVICE_STOPPED
        || observed.dwProcessId != 0
        || observed.dwWin32ExitCode != 0
    {
        return Err(RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"));
    }
    let mut result = receipt(&root, input_hash.as_deref(), action == "recover_sessions")?;
    let completed =
        super::start_intent::completed(&root, &store, &intent.intent.request_id, &command)?
            .ok_or_else(|| RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"))?;
    let acknowledged = store.acknowledge(
        &intent.intent.request_id,
        &completed.sha256,
        &super::start_intent::receipt_bytes(&root, &command)?,
    )?;
    result["start_intent_id"] = json!(intent.intent.request_id);
    result["start_intent_sha256"] = json!(acknowledged.sha256);
    Ok(result)
}

fn manage_intent(
    root: &Path,
    input: &Value,
    store: &StartStore,
    service: &Service,
) -> Result<Option<Value>, RacpError> {
    let action = input["type"].as_str().unwrap_or("");
    if action == "inspect_start_intent" {
        if input.as_object().is_none_or(|object| object.len() != 1) {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        return Ok(Some(json!({"start_intent":store.snapshot()?})));
    }
    if !["acknowledge_start_intent", "abort_start_intent"].contains(&action) {
        return Ok(None);
    }
    let object = input
        .as_object()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    let request = input["request_id"]
        .as_str()
        .filter(|id| racp_contract::service_start::id(id, "service_start_"))
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    let expected = input["expected_sha256"]
        .as_str()
        .filter(|hash| racp_contract::service_start::hash(hash))
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    if object.len() != 3
        || object
            .keys()
            .any(|key| !["type", "request_id", "expected_sha256"].contains(&key.as_str()))
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let _execution = store.execution_guard()?;
    let observed = status(service)?;
    if observed.dwCurrentState != SERVICE_STOPPED || observed.dwProcessId != 0 {
        return Err(RacpError::new("SERVICE_NOT_STOPPED"));
    }
    super::recovery::verify(service.0)?;
    if action == "abort_start_intent" {
        return Ok(Some(
            json!({"start_intent":store.abort_prepared(request, expected)?}),
        ));
    }
    if observed.dwWin32ExitCode != 0 {
        return Err(RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"));
    }
    let record = store
        .snapshot()?
        .ok_or_else(|| RacpError::new("SERVICE_START_INTENT_CONFLICT"))?;
    match &record.intent.command {
        StartCommand::Configure { input_sha256 } => {
            receipt(root, Some(input_sha256), false)?;
        }
        StartCommand::Reconcile {} => {
            receipt(root, None, false)?;
        }
        StartCommand::RecoverSessions { request_id } => {
            receipt(root, Some(request_id), true)?;
        }
        StartCommand::Maintenance { .. } => {
            return Err(RacpError::new("SERVICE_START_RECOVERY_REQUIRED"))
        }
    }
    Ok(Some(
        json!({"start_intent":store.acknowledge(request, expected, &super::start_intent::receipt_bytes(root, &record.intent.command)?)?}),
    ))
}
fn receipt(root: &Path, expected: Option<&str>, recovery: bool) -> Result<Value, RacpError> {
    if recovery {
        let raw = racp_core::read_bounded(
            &root.join(racp_runtime::providers::desktop::SESSION_RECOVERY_FILE),
            16384,
            false,
        )?;
        reject_duplicate_keys(&raw)?;
        let value: Value = serde_json::from_slice(&raw)?;
        let config = racp_core::read_bounded(
            &root.join(racp_core::native_service::CONFIG_FILE),
            16384,
            false,
        )?;
        if value["format_version"] != 1
            || value["complete"] != true
            || value["unresolved"] != json!([])
            || expected.is_none_or(|nonce| value["request_id"] != nonce)
            || value["config_sha256"] != digest(&config)
        {
            return Err(RacpError::new("SERVICE_SESSION_RECOVERY_INCOMPLETE"));
        }
        return Ok(value);
    }
    if root
        .join(racp_core::native_service::CONFIG_PENDING)
        .try_exists()?
    {
        return Err(RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"));
    }
    let raw = racp_core::read_bounded(
        &root.join(racp_core::service_config::RESULT_FILE),
        4096,
        false,
    )?;
    reject_duplicate_keys(&raw)?;
    let value: Value = serde_json::from_slice(&raw)?;
    if value["format_version"] != 1
        || value["committed"] != true
        || value["acl_verified"] != true
        || expected.is_some_and(|hash| value["input_sha256"] != hash)
    {
        return Err(RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"));
    }
    let bytes = racp_core::read_bounded(
        &root.join(racp_core::native_service::CONFIG_FILE),
        16384,
        false,
    )?;
    if value["config_sha256"] != digest(&bytes) {
        return Err(RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"));
    }
    let config: racp_core::native_service::NativeServiceConfig = serde_json::from_slice(&bytes)?;
    let parent = root
        .parent()
        .ok_or_else(|| RacpError::new("MACHINE_PATH_NOT_TRUSTED"))?;
    if racp_core::validate_local_path(parent)? != racp_core::machine_service::data_parent()? {
        return Err(RacpError::new("MACHINE_PATH_NOT_TRUSTED"));
    }
    racp_core::machine_service::grant_parent_read(parent, &config.scope)?;
    Ok(
        json!({"committed":true,"revision":value["revision"],"config_sha256":value["config_sha256"],"parent_acl_verified":true,"service_state":"stopped"}),
    )
}

fn remove_service(root: &Path, input: &Value, store: &StartStore) -> Result<Value, RacpError> {
    let object = input
        .as_object()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    if ![3, 5].contains(&object.len())
        || object.keys().any(|key| {
            ![
                "type",
                "expected_config_sha256",
                "expected_device_id",
                "resume_request_id",
                "resume_expected_sha256",
            ]
            .contains(&key.as_str())
        })
        || !input["expected_config_sha256"]
            .as_str()
            .is_some_and(|hash| {
                hash.len() == 64
                    && hash
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            })
        || !(input["expected_device_id"].is_null() || input["expected_device_id"].is_string())
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let cas = super::start_intent::resume_cas(input)?;
    let execution = store.execution_guard()?;
    let config = racp_core::read_bounded(
        &root.join(racp_core::native_service::CONFIG_FILE),
        16384,
        false,
    )?;
    if input["expected_config_sha256"] != digest(&config) {
        return Err(RacpError::new("STATE_REVISION_CONFLICT"));
    }
    let service = owned_rights(root, SERVICE_START | DELETE)?;
    super::recovery::verify(service.0)?;
    let prior = store.snapshot()?;
    let resume =
        super::start_intent::resume_maintenance(prior.as_ref(), MaintenancePurpose::Remove, cas)?;
    let (intent, command, nonce, receipt) = if resume {
        let intent = prior.unwrap();
        let observed = status(&service)?;
        if observed.dwWin32ExitCode != 0 {
            return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
        }
        let receipt = super::start_intent::maintenance_receipt(
            root,
            store,
            &intent,
            input["expected_config_sha256"].as_str().unwrap(),
            &input["expected_device_id"],
        )?;
        let StartCommand::Maintenance { request_id, .. } = &intent.intent.command else {
            unreachable!()
        };
        let nonce = request_id.clone();
        let command = intent.intent.command.clone();
        drop(execution);
        (intent, command, nonce, receipt)
    } else {
        let nonce = racp_contract::new_id("maintenance");
        let command = StartCommand::Maintenance {
            purpose: MaintenancePurpose::Remove,
            request_id: nonce.clone(),
        };
        let intent = store.prepare(command.clone())?;
        let args = [wide("--admin-intent"), wide(&intent.intent.request_id)];
        drop(execution);
        let args: Vec<_> = args.iter().map(|arg| arg.as_ptr()).collect();
        if unsafe { StartServiceW(service.0, 2, args.as_ptr()) } == 0 {
            return Err(RacpError::new("SERVICE_MAINTENANCE_START_UNKNOWN"));
        }
        let deadline = Instant::now() + Duration::from_secs(60);
        let receipt = loop {
            let observed = status(&service)?;
            if observed.dwCurrentState == SERVICE_STOPPED && observed.dwProcessId == 0 {
                if let Ok(bytes) = racp_core::read_bounded(
                    &root.join(racp_runtime::service_maintenance::RESULT_FILE),
                    4096,
                    false,
                ) {
                    reject_duplicate_keys(&bytes)?;
                    let value: Value = serde_json::from_slice(&bytes)?;
                    if value["request_id"] == nonce {
                        if observed.dwWin32ExitCode != 0
                            || value["format_version"] != 1
                            || value["preflight_complete"] != true
                            || value["config_sha256"] != input["expected_config_sha256"]
                            || value["device_id"] != input["expected_device_id"]
                        {
                            return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
                        }
                        if super::start_intent::completed(
                            root,
                            store,
                            &intent.intent.request_id,
                            &command,
                        )?
                        .is_some()
                        {
                            break value;
                        }
                    }
                }
            }
            if Instant::now() >= deadline {
                return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
            }
            std::thread::sleep(Duration::from_millis(100));
        };
        (intent, command, nonce, receipt)
    };
    // Re-open/verify the exact fixed definition after the maintenance run, then pin startup/config.
    let service = owned_rights(root, DELETE)?;
    let _execution = store.execution_guard()?;
    let current = super::start_intent::completed(root, store, &intent.intent.request_id, &command)?;
    if current
        .as_ref()
        .is_none_or(|value| resume && value.sha256 != intent.sha256)
    {
        return Err(RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"));
    }
    super::start_intent::maintenance_receipt(
        root,
        store,
        current.as_ref().unwrap(),
        input["expected_config_sha256"].as_str().unwrap(),
        &input["expected_device_id"],
    )?;
    super::recovery::verify(service.0)?;
    let _config_lock =
        racp_core::InstanceLock::acquire(&root.join(racp_core::native_service::CONFIG_LOCK))?;
    let _agent_lock = racp_core::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    // The completed preflight is historical evidence. Recheck current identity
    // under the same locks immediately before finalization, including retries.
    use racp_core::state_schema::{NativeState, SECRET_FILE, STATE_FILE, STATE_SCHEMA_VERSION};
    if root.join("native-registration.pending").try_exists()?
        || root
            .join(racp_core::native_service::CONFIG_PENDING)
            .try_exists()?
    {
        return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
    }
    if root.join(STATE_FILE).try_exists()? {
        let raw = racp_core::read_bounded(&root.join(STATE_FILE), 65536, false)?;
        reject_duplicate_keys(&raw)?;
        let state: NativeState = serde_json::from_slice(&raw)?;
        if state.schema_version != STATE_SCHEMA_VERSION
            || input["expected_device_id"] != state.device_id
            || !root.join(SECRET_FILE).try_exists()?
        {
            return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
        }
    } else if !input["expected_device_id"].is_null() || root.join(SECRET_FILE).try_exists()? {
        return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
    }
    if digest(racp_core::read_bounded(
        &root.join(racp_core::native_service::CONFIG_FILE),
        16384,
        false,
    )?) != input["expected_config_sha256"]
        || status(&service)?.dwCurrentState != SERVICE_STOPPED
        || status(&service)?.dwProcessId != 0
    {
        return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
    }
    racp_core::atomic_write(
        &root.join("native-service-detach.json"),
        &serde_json::to_vec(&json!({"format_version":1,
        "request_id":nonce,"phase":"removal_requested","maintenance":receipt}))?,
        true,
    )?;
    if unsafe { DeleteService(service.0) } == 0 {
        return Err(RacpError::new("SERVICE_REMOVAL_UNKNOWN"));
    }
    // The namespace, Device and DPAPI credential are retained; no Gateway request is made.
    Ok(
        json!({"service_removal_requested":true,"state_preserved":true,"device_preserved":true,
        "credential_preserved":true,"request_id":nonce,"deletion_visibility":"wait_for_scm_handle_release",
        "start_intent_id":intent.intent.request_id,"start_intent_retained":true}),
    )
}
