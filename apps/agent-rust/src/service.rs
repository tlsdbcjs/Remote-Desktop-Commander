//! Fixed SCM mode. Service token is verified before any protected configuration is read.
use racp_contract::RacpError;
use std::{path::PathBuf, sync::OnceLock};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{Foundation::*, System::Services::*};
struct ServiceContext {
    state: PathBuf,
    cancel: CancellationToken,
}
static CONTEXT: OnceLock<ServiceContext> = OnceLock::new();
fn name() -> Vec<u16> {
    "RACPAgent".encode_utf16().chain(Some(0)).collect()
}
pub fn run(state: PathBuf) -> Result<(), RacpError> {
    racp_core::validate_local_path(&state)?;
    CONTEXT
        .set(ServiceContext {
            state,
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
        let (settings, values) = racp_core::load_settings(&context.state, true)?;
        let _lock = racp_core::InstanceLock::acquire(&context.state.join(format!(
            "agent-{}.lock",
            racp_contract::digest(&settings.device_id)
        )))?;
        let agent = racp_runtime::Agent::new(settings, values["credential"].clone())?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?;
        report(handle, SERVICE_RUNNING, 0, 0);
        runtime.block_on(async{let running=agent.run(context.cancel.clone());tokio::pin!(running);tokio::select!{result=&mut running=>result,_=context.cancel.cancelled()=>{report(handle,SERVICE_STOP_PENDING,0,1);running.await}}})
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
