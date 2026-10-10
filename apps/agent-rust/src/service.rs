//! Fixed SCM mode. Service token is verified before any protected configuration is read.
use racp_contract::RacpError;
use std::{path::PathBuf, sync::OnceLock};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{Foundation::*, System::Services::*};
struct ServiceContext {
    state: PathBuf,
    configuration: Option<PathBuf>,
    cancel: CancellationToken,
}
static CONTEXT: OnceLock<ServiceContext> = OnceLock::new();
fn name() -> Vec<u16> {
    "RACPAgent".encode_utf16().chain(Some(0)).collect()
}
pub fn run(state: PathBuf) -> Result<(), RacpError> {
    run_inner(state, None)
}
pub fn run_config(path: PathBuf) -> Result<(), RacpError> {
    racp_core::validate_local_path(&path)?;
    let value: serde_json::Value =
        serde_json::from_slice(&racp_core::read_bounded(&path, 16384, true)?)?;
    let state = PathBuf::from(
        value["data_dir"]
            .as_str()
            .ok_or_else(|| RacpError::new("CONFIG_UNREADABLE"))?,
    );
    run_inner(state, Some(path))
}
fn run_inner(state: PathBuf, configuration: Option<PathBuf>) -> Result<(), RacpError> {
    racp_core::validate_local_path(&state)?;
    CONTEXT
        .set(ServiceContext {
            state,
            configuration,
            cancel: CancellationToken::new(),
        })
        .map_err(|_| RacpError::new("RESOURCE_BUSY"))?;
    let mut service_name = name();
    let entries = [
        SERVICE_TABLE_ENTRYW {
            lpServiceName: service_name.as_mut_ptr(),
            lpServiceProc: Some(main),
        },
        SERVICE_TABLE_ENTRYW {
            lpServiceName: std::ptr::null_mut(),
            lpServiceProc: None,
        },
    ];
    if unsafe { StartServiceCtrlDispatcherW(entries.as_ptr()) } == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}
unsafe extern "system" fn control(
    code: u32,
    _: u32,
    _: *mut core::ffi::c_void,
    _: *mut core::ffi::c_void,
) -> u32 {
    if matches!(code, SERVICE_CONTROL_STOP | SERVICE_CONTROL_SHUTDOWN) {
        if let Some(context) = CONTEXT.get() {
            context.cancel.cancel();
        }
    }
    0
}
unsafe fn report(handle: SERVICE_STATUS_HANDLE, state: u32, error: u32, checkpoint: u32) {
    let status = SERVICE_STATUS {
        dwServiceType: SERVICE_WIN32_OWN_PROCESS,
        dwCurrentState: state,
        dwControlsAccepted: if state == SERVICE_RUNNING {
            SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
        } else {
            0
        },
        dwWin32ExitCode: error,
        dwServiceSpecificExitCode: 0,
        dwCheckPoint: checkpoint,
        dwWaitHint: if matches!(state, SERVICE_START_PENDING | SERVICE_STOP_PENDING) {
            30000
        } else {
            0
        },
    };
    SetServiceStatus(handle, &status);
}
fn verify_service_identity() -> Result<(), RacpError> {
    use windows_sys::Win32::Security::*;
    let peer =
        racp_runtime::providers::desktop::native_identity::PinnedPeer::open(std::process::id())?;
    if peer.identity().session != 0
        || peer.identity().sid == "S-1-5-18"
        || peer.identity().administrator
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let account: Vec<u16> = "NT SERVICE\\RACPAgent"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let mut sid = [0u8; 128];
    let mut sid_size = 128;
    let mut domain = [0u16; 256];
    let mut domain_size = 256;
    let mut kind = 0;
    if unsafe {
        LookupAccountNameW(
            std::ptr::null(),
            account.as_ptr(),
            sid.as_mut_ptr().cast(),
            &mut sid_size,
            domain.as_mut_ptr(),
            &mut domain_size,
            &mut kind,
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut text = std::ptr::null_mut();
    if unsafe {
        windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW(
            sid.as_mut_ptr().cast(),
            &mut text,
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let expected = unsafe {
        let mut len = 0;
        while *text.add(len) != 0 {
            len += 1;
        }
        let value = String::from_utf16_lossy(std::slice::from_raw_parts(text, len));
        LocalFree(text.cast());
        value
    };
    if !peer.identity().service_sids.contains(&expected) {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}
unsafe extern "system" fn main(_: u32, _: *mut *mut u16) {
    let handle =
        RegisterServiceCtrlHandlerExW(name().as_ptr(), Some(control), std::ptr::null_mut());
    if handle.is_null() {
        return;
    }
    report(handle, SERVICE_START_PENDING, 0, 1);
    let result = (|| -> Result<(), RacpError> {
        verify_service_identity()?;
        let context = CONTEXT
            .get()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        racp_runtime::providers::desktop::verify_config_identity(&context.state)?;
        let (settings, credential, browser, credential_state) =
            if let Some(config) = &context.configuration {
                configured(config)?
            } else {
                let (settings, values) = racp_core::load_settings(&context.state, true)?;
                (
                    settings,
                    values["credential"].clone(),
                    racp_runtime::providers::browser::BrowserConfig::bundled(),
                    context.state.clone(),
                )
            };
        if settings.data_dir != context.state && context.configuration.is_some() {
            return Err(RacpError::new("SETTINGS_CHANGED"));
        }
        let _data_lock = racp_core::InstanceLock::acquire(&settings.data_dir.join("agent.lock"))?;
        let _lock = racp_core::InstanceLock::acquire(&credential_state.join(format!(
            "agent-{}.lock",
            racp_contract::digest(&settings.device_id)
        )))?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?;
        runtime.block_on(async {
            let agent = racp_runtime::Agent::with_browser(settings, credential, browser)?;
            report(handle, SERVICE_RUNNING, 0, 0);
            let running = agent.run(context.cancel.clone());
            tokio::pin!(running);
            tokio::select! {
                result = &mut running => result,
                _ = context.cancel.cancelled() => {
                    report(handle, SERVICE_STOP_PENDING, 0, 1);
                    running.await
                }
            }
        })
    })();
    report(
        handle,
        SERVICE_STOPPED,
        if result.is_ok() {
            0
        } else {
            ERROR_SERVICE_SPECIFIC_ERROR
        },
        0,
    );
}

fn configured(
    path: &std::path::Path,
) -> Result<
    (
        racp_core::AgentSettings,
        String,
        racp_runtime::providers::browser::BrowserConfig,
        PathBuf,
    ),
    RacpError,
> {
    let mut config: serde_json::Value =
        serde_json::from_slice(&racp_core::read_bounded(path, 16384, true)?)?;
    let schema: serde_json::Value = serde_json::from_str(include_str!(
        "../../../docs/protocol/agent-service-config-v1.schema.json"
    ))?;
    racp_contract::validate_schema(&schema, &config)?;
    for (key, value) in [
        ("service_name", serde_json::json!("RACPAgent")),
        ("allowed_workspaces", serde_json::json!([])),
        ("desktop_login_users", serde_json::json!([])),
        ("profile", serde_json::json!("read_only")),
        ("browser_cdp", serde_json::json!(false)),
        ("browser_allowed_origins", serde_json::json!([])),
    ] {
        if config.get(key).is_none() {
            config[key] = value;
        }
    }
    let actor =
        racp_runtime::providers::desktop::native_identity::PinnedPeer::open(std::process::id())?;
    if config["service_name"] != "RACPAgent"
        || config["agent_sid"] != actor.identity().sid
        || !actor
            .identity()
            .service_sids
            .contains(config["service_sid"].as_str().unwrap_or(""))
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let credentials = PathBuf::from(
        config["credentials"]
            .as_str()
            .ok_or_else(|| RacpError::new("CONFIG_UNREADABLE"))?,
    );
    racp_core::validate_local_path(&credentials)?;
    let credential_state = credentials
        .parent()
        .ok_or_else(|| RacpError::new("CONFIG_UNREADABLE"))?
        .to_owned();
    let stored = racp_core::SecretStore::new(credentials).load()?;
    let get = |name: &str| {
        stored
            .get(name)
            .cloned()
            .ok_or_else(|| RacpError::new("CONFIG_UNREADABLE"))
    };
    let mut settings: racp_core::AgentSettings = serde_json::from_value(
        serde_json::json!({"version":2,"gateway":get("gateway")?,"device_id":get("device_id")?,"workspace":config["workspace"],"data_dir":config["data_dir"],"allowed_workspaces":config["allowed_workspaces"],"profile":config["profile"],"ca_file":config["ca_file"],"desktop_enabled":config["desktop_login_users"].as_array().is_some_and(|a|!a.is_empty()),"permissions":null}),
    )?;
    if settings.ca_file.is_none() {
        settings.ca_file = stored.get("ca_file").map(PathBuf::from);
    }
    settings.validate(true)?;
    if let Some(plugin) = config["plugin_config"].as_str() {
        let raw = racp_core::read_bounded(std::path::Path::new(plugin), 65536, true)?;
        let target = settings.data_dir.join("plugins.json");
        if target.try_exists()? {
            if racp_core::read_bounded(&target, 65536, true)? != raw {
                return Err(RacpError::new("CONFLICT"));
            }
        } else {
            racp_core::atomic_write(&target, &raw, false)?;
        }
    }
    if settings.desktop_enabled && !settings.data_dir.join("service-login.json").try_exists()? {
        let mut args = vec![
            "--device-id".into(),
            settings.device_id.clone(),
            "--agent-sid".into(),
            actor.identity().sid.clone(),
            "--service-sid".into(),
            config["service_sid"].as_str().unwrap().into(),
            "--endpoint".into(),
            settings
                .data_dir
                .join("desktop-login-endpoint.json")
                .to_string_lossy()
                .into_owned(),
        ];
        for user in config["desktop_login_users"].as_array().unwrap() {
            args.push("--login-user".into());
            args.push(user.as_str().unwrap().into());
        }
        racp_runtime::providers::desktop::configure_login(&args, &settings.data_dir)?;
    }
    let mut browser = racp_runtime::providers::browser::BrowserConfig::bundled();
    browser.cdp_enabled = config["browser_cdp"] == true;
    browser.allow_origins = config["browser_allowed_origins"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str().map(str::to_owned))
        .collect();
    Ok((settings, get("credential")?, browser, credential_state))
}
