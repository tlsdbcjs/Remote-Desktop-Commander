use racp_contract::service_recovery::{RecoveryAction, ServiceRecoveryPolicy};
use serde_json::json;

#[test]
fn default_recovery_is_disabled_until_maintenance_and_stop_gates_exist() {
    let policy = ServiceRecoveryPolicy::default();
    policy.validate().unwrap();
    assert_eq!(policy.actions, vec![RecoveryAction::None {}]);
    assert!(!policy.on_non_crash_failures);
}

#[test]
fn bounded_crash_recovery_has_a_terminal_none_for_all_later_failures() {
    let policy = ServiceRecoveryPolicy::bounded_crash_only();
    policy.validate().unwrap();
    assert!(!policy.on_non_crash_failures);
    assert_eq!(policy.reset_after_seconds, 86400);
    assert_eq!(
        policy.actions,
        vec![
            RecoveryAction::Restart { delay_ms: 5000 },
            RecoveryAction::Restart { delay_ms: 30000 },
            RecoveryAction::Restart { delay_ms: 120000 },
            RecoveryAction::None {},
        ]
    );
    // SCM repeats the last entry after the action list is exhausted.
    assert_eq!(policy.actions.last(), Some(&RecoveryAction::None {}));
}

#[test]
fn infinite_restart_reported_error_recovery_and_unbounded_delays_are_rejected() {
    let original = ServiceRecoveryPolicy::bounded_crash_only();
    let mut policy = original.clone();
    policy.actions.pop();
    assert!(policy.validate().is_err());
    let mut policy = original.clone();
    policy.on_non_crash_failures = true;
    assert!(policy.validate().is_err());
    for delay_ms in [0, 3600001] {
        let mut policy = original.clone();
        policy.actions[0] = RecoveryAction::Restart { delay_ms };
        assert!(policy.validate().is_err());
    }
    let mut policy = original;
    policy.reset_after_seconds = 1;
    assert!(policy.validate().is_err());
}

#[test]
fn command_reboot_and_unknown_recovery_fields_are_not_contract_actions() {
    for action in [
        json!({"type":"run_command","command":"untrusted.exe"}),
        json!({"type":"reboot"}),
        json!({"type":"none","delay_ms":1}),
    ] {
        assert!(serde_json::from_value::<RecoveryAction>(action).is_err());
    }
    let mut policy = serde_json::to_value(ServiceRecoveryPolicy::bounded_crash_only()).unwrap();
    policy["command"] = json!("untrusted.exe");
    assert!(serde_json::from_value::<ServiceRecoveryPolicy>(policy).is_err());
}
