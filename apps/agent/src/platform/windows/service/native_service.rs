//! Separate native SCM identity/state. Never reads the legacy RACPAgent config.
use racp_contract::{service_scope::SERVICE_NAME, RacpError};
use std::{path::PathBuf, sync::OnceLock};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{Foundation::*, System::Services::*};
struct Context {
    root: PathBuf,
    cancel: CancellationToken,
    bootstrap_hash: Option<String>,
}
static CONTEXT: OnceLock<Context> = OnceLock::new();
fn name() -> Vec<u16> {
    SERVICE_NAME.encode_utf16().chain(Some(0)).collect()
}
pub fn run(root: PathBuf, bootstrap_hash: Option<String>) -> Result<(), RacpError> {
    if bootstrap_hash
        .as_ref()
        .is_some_and(|hash| hash.len() != 64 || !hash.bytes().all(|v| v.is_ascii_hexdigit()))
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    racp_core::validate_local_path(&root)?;
    CONTEXT
        .set(Context {
            root,
            cancel: CancellationToken::new(),
            bootstrap_hash,
        })
        .map_err(|_| RacpError::new("RESOURCE_BUSY"))?;
    let mut name = name();
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
        return Err(RacpError::new("SERVICE_HOST_UNAVAILABLE"));
    }
    Ok(())
}
unsafe extern "system" fn control(
    code: u32,
    _: u32,
    _: *mut core::ffi::c_void,
    _: *mut core::ffi::c_void,
) -> u32 {
    match code {
        SERVICE_CONTROL_STOP | SERVICE_CONTROL_SHUTDOWN => {
            if let Some(context) = CONTEXT.get() {
                context.cancel.cancel();
            }
            ERROR_SUCCESS
        }
        SERVICE_CONTROL_INTERROGATE => ERROR_SUCCESS,
        _ => ERROR_CALL_NOT_IMPLEMENTED,
    }
}
fn report(handle: SERVICE_STATUS_HANDLE, state: u32, error: u32, checkpoint: u32) {
    let pending = matches!(state, SERVICE_START_PENDING | SERVICE_STOP_PENDING);
    let status = SERVICE_STATUS {
        dwServiceType: SERVICE_WIN32_OWN_PROCESS,
        dwCurrentState: state,
        dwControlsAccepted: if state == SERVICE_RUNNING {
            SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
        } else {
            0
        },
        dwWin32ExitCode: if error == 0 {
            0
        } else {
            ERROR_SERVICE_SPECIFIC_ERROR
        },
        dwServiceSpecificExitCode: error,
        dwCheckPoint: if pending { checkpoint } else { 0 },
        dwWaitHint: if pending { 30000 } else { 0 },
    };
    unsafe {
        SetServiceStatus(handle, &status);
    }
}
fn verify() -> Result<(), RacpError> {
    let peer =
        racp_runtime::providers::desktop::native_identity::PinnedPeer::open(std::process::id())?;
    let sid = racp_core::service_directory::expected_service_sid()?;
    let identity = peer.identity();
    if identity.session != 0
        || identity.sid != sid
        || identity.administrator
        || !identity.service_sids.contains(&sid)
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}
enum StartMode {
    Run,
    Configure(String),
    Reconcile,
    RecoverSessions(String),
    Maintenance(String),
}
unsafe fn start_mode(count: u32, args: *mut *mut u16) -> Result<StartMode, RacpError> {
    if count <= 1 {
        return Ok(StartMode::Run);
    }
    if count > 3 || args.is_null() {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let mut values = vec![];
    for index in 1..count as usize {
        let ptr = *args.add(index);
        if ptr.is_null() {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        let mut length = 0;
        while length <= 128 && *ptr.add(length) != 0 {
            length += 1;
        }
        if length > 128 {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        values.push(
            String::from_utf16(std::slice::from_raw_parts(ptr, length))
                .map_err(|_| RacpError::new("REQUEST_INVALID"))?,
        );
    }
    match values.as_slice() {
        [flag, nonce]
            if flag == "--maintenance"
                && nonce.strip_prefix("maintenance_").is_some_and(|id| {
                    id.len() == 32
                        && id
                            .bytes()
                            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                }) =>
        {
            Ok(StartMode::Maintenance(nonce.clone()))
        }
        [flag] if flag == "--reconcile-config" => Ok(StartMode::Reconcile),
        [flag, nonce]
            if flag == "--recover-sessions"
                && nonce.strip_prefix("session_recovery_").is_some_and(|id| {
                    id.len() == 32
                        && id
                            .bytes()
                            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                }) =>
        {
            Ok(StartMode::RecoverSessions(nonce.clone()))
        }
        [flag, hash]
            if flag == "--configure-sha256"
                && hash.len() == 64
                && hash.bytes().all(|v| v.is_ascii_hexdigit()) =>
        {
            Ok(StartMode::Configure(hash.clone()))
        }
        _ => Err(RacpError::new("REQUEST_INVALID")),
    }
}
unsafe extern "system" fn main(count: u32, args: *mut *mut u16) {
    let handle =
        RegisterServiceCtrlHandlerExW(name().as_ptr(), Some(control), std::ptr::null_mut());
    if handle.is_null() {
        return;
    }
    report(handle, SERVICE_START_PENDING, 0, 1);
    let result = (|| -> Result<(), RacpError> {
        verify()?;
        let mode = start_mode(count, args)?;
        let _payload = racp_core::distribution::pin_current_agent_service_payload()?;
        let exe = std::env::current_exe()?;
        let _image = racp_core::machine_service::pin_image_directory(
            exe.parent()
                .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?,
        )?;
        let context = CONTEXT
            .get()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?;
        runtime.block_on(async {
            let (ready, mut readiness) = tokio::sync::oneshot::channel();
            let running = async {
                match mode {
                    StartMode::Maintenance(nonce) => {
                        let root=context.root.clone();
                        if context.cancel.is_cancelled() {return Err(RacpError::new("CANCELLED"));}
                        let _=ready.send(());
                        return tokio::task::spawn_blocking(move ||racp_runtime::service_maintenance::inspect(&root,&nonce)).await
                            .map_err(|_|RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"))?;
                    },
                    StartMode::RecoverSessions(nonce) => {
                        let root=context.root.clone();
                        let cancel=context.cancel.clone();
                        if context.cancel.is_cancelled() {return Err(RacpError::new("CANCELLED"));}
                        let _=ready.send(());
                        return tokio::task::spawn_blocking(move ||
                            racp_runtime::providers::desktop::recover_service_sessions(&root,&nonce,cancel).map(|_|()))
                            .await.map_err(|_|RacpError::new("SERVICE_SESSION_RECOVERY_INCOMPLETE"))?;
                    },
                    StartMode::Configure(hash) => {
                        let root = context.root.clone();
                        if context.cancel.is_cancelled() { return Err(RacpError::new("CANCELLED")); }
                        let _ = ready.send(());
                        return tokio::task::spawn_blocking(move || racp_core::service_config::update(&root,&hash)).await
                            .map_err(|_| RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"))?;
                    },
                    StartMode::Reconcile => {
                        let root = context.root.clone();
                        if context.cancel.is_cancelled() { return Err(RacpError::new("CANCELLED")); }
                        let _ = ready.send(());
                        return tokio::task::spawn_blocking(move || racp_core::service_config::reconcile(&root)).await
                            .map_err(|_| RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"))?;
                    },
                    StartMode::Run => {},
                }
                racp_runtime::service_bootstrap::prepare(&context.root, context.bootstrap_hash.as_deref(), &context.cancel).await?;
                racp_runtime::native_client::run_service(&context.root, context.cancel.clone(), ready).await
            };
            tokio::pin!(running);
            let mut timer = tokio::time::interval(std::time::Duration::from_secs(5));
            let mut started = false;
            let mut checkpoint = 1u32;
            loop {
                tokio::select! {
                    result = &mut running => break result,
                    result = &mut readiness, if !started => {
                        if result.is_err() {
                            context.cancel.cancel();
                            break running.await;
                        }
                        started = true;
                        if !context.cancel.is_cancelled() { report(handle, SERVICE_RUNNING, 0, 0); }
                    },
                    _ = timer.tick() => {
                        if context.cancel.is_cancelled() { report(handle, SERVICE_STOP_PENDING, 0, checkpoint); }
                        else if !started { report(handle, SERVICE_START_PENDING, 0, checkpoint); }
                        checkpoint = checkpoint.saturating_add(1);
                    }
                }
            }
        })
    })();
    report(
        handle,
        SERVICE_STOPPED,
        if result.is_ok() { 0 } else { 4 },
        0,
    );
}
