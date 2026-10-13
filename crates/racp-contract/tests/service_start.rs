use racp_contract::service_start::*;
use serde_json::json;

#[test]
fn start_commands_accept_only_bounded_known_modes_and_nonce_grammars() {
    for command in [
        StartCommand::Configure {
            input_sha256: "a".repeat(64),
        },
        StartCommand::Reconcile {},
        StartCommand::RecoverSessions {
            request_id: racp_contract::new_id("session_recovery"),
        },
        StartCommand::Maintenance {
            purpose: MaintenancePurpose::Restore,
            request_id: racp_contract::new_id("maintenance"),
        },
    ] {
        command.validate().unwrap();
    }
    for value in [
        json!({"type":"reconcile","command":"untrusted.exe"}),
        json!({"type":"run"}),
        json!({"type":"maintenance","purpose":"adopt","request_id":"maintenance_bad"}),
    ] {
        assert!(serde_json::from_value::<StartCommand>(value).is_err());
    }
    assert!(StartCommand::Configure {
        input_sha256: "A".repeat(64)
    }
    .validate()
    .is_err());
    assert!(StartCommand::RecoverSessions {
        request_id: "session_recovery_bad".into()
    }
    .validate()
    .is_err());
}

#[test]
fn unconfirmed_phases_never_permit_a_normal_service_start() {
    let mut intent = StartIntent {
        format_version: 1,
        root_identity: "1:2".into(),
        request_id: racp_contract::new_id("service_start"),
        command: StartCommand::Reconcile {},
        phase: StartPhase::Prepared {},
    };
    let attempt_id = racp_contract::new_id("service_attempt");
    for phase in [
        StartPhase::Prepared {},
        StartPhase::Claimed {
            attempt_id: attempt_id.clone(),
        },
        StartPhase::Failed {
            attempt_id: attempt_id.clone(),
            error_code: "SERVICE_CONFIG_OUTCOME_UNKNOWN".into(),
        },
        StartPhase::Completed {
            attempt_id: attempt_id.clone(),
            receipt_sha256: "a".repeat(64),
        },
    ] {
        intent.phase = phase;
        intent.validate().unwrap();
        assert!(!intent.permits_normal_run());
    }
    intent.phase = StartPhase::Acknowledged {
        attempt_id,
        receipt_sha256: "a".repeat(64),
    };
    intent.validate().unwrap();
    assert!(intent.permits_normal_run());
    intent.root_identity = "foreign:root".into();
    assert!(intent.validate().is_err());
}
