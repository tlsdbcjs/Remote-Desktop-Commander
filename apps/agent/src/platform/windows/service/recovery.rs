//! Called only for a freshly created fixed-name service, never for an adopted service.
use racp_contract::{
    service_recovery::{RecoveryAction, ServiceRecoveryPolicy},
    RacpError,
};
use windows_sys::Win32::System::Services::*;

fn unknown() -> RacpError {
    RacpError::new("SERVICE_RECOVERY_OUTCOME_UNKNOWN")
}

fn native_actions() -> Vec<SC_ACTION> {
    // Restart is deliberately not enabled until administrative crash/queued-stop gates exist.
    actions_for_policy(ServiceRecoveryPolicy::default())
}

fn actions_for_policy(policy: ServiceRecoveryPolicy) -> Vec<SC_ACTION> {
    policy
        .actions
        .into_iter()
        .map(|action| match action {
            RecoveryAction::Restart { delay_ms } => SC_ACTION {
                Type: SC_ACTION_RESTART,
                Delay: delay_ms,
            },
            RecoveryAction::None {} => SC_ACTION {
                Type: SC_ACTION_NONE,
                Delay: 0,
            },
        })
        .collect()
}

fn verify_actions_buffer(words: &[usize], used: usize) -> Result<(), RacpError> {
    let header_size = std::mem::size_of::<SERVICE_FAILURE_ACTIONSW>();
    if used < header_size || used > std::mem::size_of_val(words) {
        return Err(unknown());
    }
    let begin = words.as_ptr() as usize;
    let end = begin.checked_add(used).ok_or_else(unknown)?;
    // Aligned storage and validated extents precede every native pointer dereference.
    let header = unsafe { &*words.as_ptr().cast::<SERVICE_FAILURE_ACTIONSW>() };
    let expected = native_actions();
    if header.dwResetPeriod != ServiceRecoveryPolicy::default().reset_after_seconds
        || header.cActions as usize != expected.len()
    {
        return Err(unknown());
    }
    let address = header.lpsaActions as usize;
    let bytes = expected.len() * std::mem::size_of::<SC_ACTION>();
    if address < begin + header_size
        || address % std::mem::align_of::<SC_ACTION>() != 0
        || address.checked_add(bytes).is_none_or(|limit| limit > end)
    {
        return Err(unknown());
    }
    let actual = unsafe { std::slice::from_raw_parts(header.lpsaActions, expected.len()) };
    if actual
        .iter()
        .zip(&expected)
        .any(|(a, e)| a.Type != e.Type || a.Delay != e.Delay)
    {
        return Err(unknown());
    }
    for text in [header.lpCommand, header.lpRebootMsg] {
        if text.is_null() {
            continue;
        }
        let address = text as usize;
        if address < begin + header_size
            || address % 2 != 0
            || address.checked_add(2).is_none_or(|limit| limit > end)
            || unsafe { *text } != 0
        {
            return Err(unknown());
        }
    }
    Ok(())
}

fn read_actions(
    mut query: impl FnMut(*mut u8, u32, &mut u32) -> Result<(), u32>,
) -> Result<(), RacpError> {
    let mut needed = 0;
    if query(std::ptr::null_mut(), 0, &mut needed)
        != Err(windows_sys::Win32::Foundation::ERROR_INSUFFICIENT_BUFFER)
        || needed < std::mem::size_of::<SERVICE_FAILURE_ACTIONSW>() as u32
        || needed > 8192
    {
        return Err(unknown());
    }
    let capacity = needed;
    let mut words = vec![0usize; (capacity as usize).div_ceil(std::mem::size_of::<usize>())];
    let mut ignored = 0;
    query(words.as_mut_ptr().cast(), capacity, &mut ignored).map_err(|_| unknown())?;
    verify_actions_buffer(&words, capacity as usize)
}

pub(super) fn verify(service: SC_HANDLE) -> Result<(), RacpError> {
    read_actions(|pointer, size, needed| {
        if unsafe {
            QueryServiceConfig2W(
                service,
                SERVICE_CONFIG_FAILURE_ACTIONS,
                pointer,
                size,
                needed,
            )
        } == 0
        {
            Err(unsafe { windows_sys::Win32::Foundation::GetLastError() })
        } else {
            Ok(())
        }
    })?;
    let mut flag = SERVICE_FAILURE_ACTIONS_FLAG::default();
    let mut needed = 0;
    if unsafe {
        QueryServiceConfig2W(
            service,
            SERVICE_CONFIG_FAILURE_ACTIONS_FLAG,
            (&mut flag as *mut SERVICE_FAILURE_ACTIONS_FLAG).cast(),
            std::mem::size_of_val(&flag) as u32,
            &mut needed,
        )
    } == 0
        || flag.fFailureActionsOnNonCrashFailures != 0
    {
        return Err(unknown());
    }
    Ok(())
}

pub(super) fn configure(service: SC_HANDLE) -> Result<(), RacpError> {
    racp_core::machine_service::require_system()?;
    let policy = ServiceRecoveryPolicy::default();
    policy.validate()?;
    let flag = SERVICE_FAILURE_ACTIONS_FLAG {
        fFailureActionsOnNonCrashFailures: 0,
    };
    let mut actions = native_actions();
    // Empty strings explicitly remove command/reboot text; NULL would preserve it.
    let mut empty = [0u16];
    let config = SERVICE_FAILURE_ACTIONSW {
        dwResetPeriod: policy.reset_after_seconds,
        lpRebootMsg: empty.as_mut_ptr(),
        lpCommand: empty.as_mut_ptr(),
        cActions: actions.len() as u32,
        lpsaActions: actions.as_mut_ptr(),
    };
    if unsafe {
        ChangeServiceConfig2W(
            service,
            SERVICE_CONFIG_FAILURE_ACTIONS_FLAG,
            (&flag as *const SERVICE_FAILURE_ACTIONS_FLAG).cast(),
        )
    } == 0
        || unsafe {
            ChangeServiceConfig2W(
                service,
                SERVICE_CONFIG_FAILURE_ACTIONS,
                (&config as *const SERVICE_FAILURE_ACTIONSW).cast(),
            )
        } == 0
    {
        return Err(unknown());
    }
    verify(service)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fresh_service_default_cannot_restart_a_crashed_one_shot_admin_operation() {
        let actions = native_actions();
        assert_eq!(actions.len(), 1);
        assert_eq!(actions[0].Type, SC_ACTION_NONE);
        assert_eq!(actions[0].Delay, 0);
    }
    #[test]
    fn bounded_profile_maps_to_three_restarts_and_terminal_none_without_applying_it() {
        let actions = actions_for_policy(ServiceRecoveryPolicy::bounded_crash_only());
        assert_eq!(actions.len(), 4);
        for (action, delay) in actions[..3].iter().zip([5000, 30000, 120000]) {
            assert_eq!(action.Type, SC_ACTION_RESTART);
            assert_eq!(action.Delay, delay);
        }
        assert_eq!(actions[3].Type, SC_ACTION_NONE);
    }
    #[test]
    fn scm_query_uses_probe_size_when_success_does_not_set_needed() {
        let mut calls = 0;
        read_actions(|pointer, size, needed| {
            calls += 1;
            if pointer.is_null() {
                assert_eq!(size, 0);
                *needed = 512;
                return Err(windows_sys::Win32::Foundation::ERROR_INSUFFICIENT_BUFFER);
            }
            assert_eq!(size, 512);
            let words = buffer();
            unsafe {
                std::ptr::copy_nonoverlapping(words.as_ptr().cast::<u8>(), pointer, 512);
                (*pointer.cast::<SERVICE_FAILURE_ACTIONSW>()).lpsaActions = pointer
                    .add(std::mem::size_of::<SERVICE_FAILURE_ACTIONSW>())
                    .cast();
            }
            *needed = 0; // Success has no specified pcbBytesNeeded value.
            Ok(())
        })
        .unwrap();
        assert_eq!(calls, 2);
    }
    #[test]
    fn scm_query_rejects_unbounded_size_and_probe_failure_before_reading() {
        let mut calls = 0;
        assert!(read_actions(|_, _, needed| {
            calls += 1;
            *needed = 8193;
            Err(windows_sys::Win32::Foundation::ERROR_INSUFFICIENT_BUFFER)
        })
        .is_err());
        assert_eq!(calls, 1);
        assert!(
            read_actions(|_, _, _| Err(windows_sys::Win32::Foundation::ERROR_ACCESS_DENIED))
                .is_err()
        );
    }
    fn buffer() -> Vec<usize> {
        let mut words = vec![0usize; 64];
        let base = words.as_mut_ptr().cast::<u8>();
        let actions = native_actions();
        let action_pointer = unsafe {
            base.add(std::mem::size_of::<SERVICE_FAILURE_ACTIONSW>())
                .cast::<SC_ACTION>()
        };
        unsafe {
            std::ptr::copy_nonoverlapping(actions.as_ptr(), action_pointer, actions.len());
            base.cast::<SERVICE_FAILURE_ACTIONSW>()
                .write(SERVICE_FAILURE_ACTIONSW {
                    dwResetPeriod: 86400,
                    lpRebootMsg: std::ptr::null_mut(),
                    lpCommand: std::ptr::null_mut(),
                    cActions: actions.len() as u32,
                    lpsaActions: action_pointer,
                });
        }
        words
    }
    #[test]
    fn scm_readback_requires_disabled_terminal_action_and_reset_period() {
        let mut words = buffer();
        verify_actions_buffer(&words, 512).unwrap();
        unsafe {
            let header = &mut *words.as_mut_ptr().cast::<SERVICE_FAILURE_ACTIONSW>();
            (*header.lpsaActions).Type = SC_ACTION_RESTART;
        }
        assert!(verify_actions_buffer(&words, 512).is_err());
        let mut words = buffer();
        unsafe {
            (*words.as_mut_ptr().cast::<SERVICE_FAILURE_ACTIONSW>()).dwResetPeriod = 0;
        }
        assert!(verify_actions_buffer(&words, 512).is_err());
    }
    #[test]
    fn scm_readback_rejects_untrusted_counts_pointers_and_truncated_buffers() {
        let mut words = buffer();
        assert!(verify_actions_buffer(&words, 1).is_err());
        assert!(verify_actions_buffer(&words, usize::MAX).is_err());
        unsafe {
            (*words.as_mut_ptr().cast::<SERVICE_FAILURE_ACTIONSW>()).cActions = u32::MAX;
        }
        assert!(verify_actions_buffer(&words, 512).is_err());
        let mut words = buffer();
        unsafe {
            (*words.as_mut_ptr().cast::<SERVICE_FAILURE_ACTIONSW>()).lpsaActions =
                std::ptr::dangling_mut();
        }
        assert!(verify_actions_buffer(&words, 512).is_err());
    }
    #[test]
    fn scm_readback_rejects_commands_and_reboot_messages() {
        for reboot in [false, true] {
            let mut words = buffer();
            unsafe {
                let text = words.as_mut_ptr().cast::<u16>().add(240);
                *text = b'x' as u16;
                let header = &mut *words.as_mut_ptr().cast::<SERVICE_FAILURE_ACTIONSW>();
                if reboot {
                    header.lpRebootMsg = text;
                } else {
                    header.lpCommand = text;
                }
            }
            assert!(verify_actions_buffer(&words, 512).is_err());
        }
        let mut words = buffer();
        unsafe {
            (*words.as_mut_ptr().cast::<SERVICE_FAILURE_ACTIONSW>()).lpCommand =
                std::ptr::dangling_mut();
        }
        assert!(verify_actions_buffer(&words, 512).is_err());
    }
}
