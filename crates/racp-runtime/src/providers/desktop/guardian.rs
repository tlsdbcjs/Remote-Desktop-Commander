//! Release-only watchdog is launched by the Agent before the Broker Job exists.
use super::{
    hooks::{self, NativeWatch},
    native_identity::PinnedPeer,
    PairConfig,
};
use racp_contract::RacpError;
use serde_json::json;
use std::{
    path::Path,
    time::{Duration, Instant},
};
use windows_sys::Win32::System::{
    JobObjects::IsProcessInJob,
    Threading::{CreateMutexW, GetCurrentProcess, ReleaseMutex},
};
pub fn run_guardian(path: &Path) -> Result<(), RacpError> {
    racp_core::validate_local_path(path)?;
    let document = racp_core::SecretStore::new(path.into()).load()?;
    let pair = PairConfig::decode(
        document
            .get("pair")
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?
            .as_bytes(),
    )?;
    let _query_grants = if let Some(service) = &pair.agent_service_sid {
        Some(super::access::own(&[service.clone()], false)?)
    } else {
        None
    };
    let identity = PinnedPeer::open(std::process::id())?;
    pair.require_broker(identity.identity())?;
    let controller = PinnedPeer::open(pair.agent_pid)?;
    pair.require_agent(controller.identity())?;
    let mut inside = 0;
    if unsafe { IsProcessInJob(GetCurrentProcess(), std::ptr::null_mut(), &mut inside) } == 0
        || inside != 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let gate_name: Vec<u16> = format!(
        "Local\\RACP-InputGuardian-{}-{}",
        pair.session_id, pair.user_sid
    )
    .encode_utf16()
    .chain(Some(0))
    .collect();
    let gate = unsafe { CreateMutexW(std::ptr::null(), 1, gate_name.as_ptr()) };
    if gate.is_null() {
        return Err(RacpError::new("SESSION_UNAVAILABLE"));
    }
    let already = unsafe { windows_sys::Win32::Foundation::GetLastError() }
        == windows_sys::Win32::Foundation::ERROR_ALREADY_EXISTS;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    let gate = unsafe { OwnedHandle::from_raw_handle(gate) };
    if already {
        return Err(RacpError::new("RESOURCE_BUSY"));
    }
    let watch = NativeWatch::new(hooks::marker(&pair.secret), true)?;
    let status = path.with_file_name("guardian-status.json");
    let mut stopping = None;
    loop {
        let stop = controller.alive().is_err()
            || !path.try_exists()?
            || path.with_file_name("guardian-stop").try_exists()?;
        if stop && stopping.is_none() {
            stopping = Some(Instant::now());
        }
        if stopping.is_some() {
            watch
                .state
                .lock()
                .map_err(|_| RacpError::new("SESSION_UNAVAILABLE"))?
                .release_requested = true;
        }
        let (healthy, pending) = {
            let state = watch
                .state
                .lock()
                .map_err(|_| RacpError::new("SESSION_UNAVAILABLE"))?;
            (
                state.healthy && state.counter.healthy(),
                state.held.snapshot().len(),
            )
        };
        let value = json!({"pid":identity.identity().pid,"create_time":identity.identity().created,"healthy":healthy,"held_count":pending,"outside_all_jobs":true,"timestamp":racp_contract::timestamp(),"cleanup_confirmed":stopping.is_some()&&pending==0&&healthy});
        racp_core::atomic_write(&status, &serde_json::to_vec(&value)?, true)?;
        if stopping.is_some_and(|at| at.elapsed() >= Duration::from_millis(200))
            && pending == 0
            && healthy
        {
            break;
        }
        // Failed release intentionally keeps the independent watchdog alive to retry.
        std::thread::sleep(Duration::from_millis(50));
    }
    unsafe {
        ReleaseMutex(gate.as_raw_handle());
    }
    Ok(())
}
