//! The service actor consumes only a private, root-bound administrative intent.
use racp_contract::{service_start::StartCommand, RacpError};
use racp_core::{service_start::StartStore, Workspaces};
use std::path::Path;
use tokio_util::sync::CancellationToken;

pub(super) fn resume_cas(input: &serde_json::Value) -> Result<Option<(&str, &str)>, RacpError> {
    match (
        input.get("resume_request_id"),
        input.get("resume_expected_sha256"),
    ) {
        (None, None) => Ok(None),
        (Some(id), Some(hash)) => {
            let id = id
                .as_str()
                .filter(|v| racp_contract::service_start::id(v, "service_start_"));
            let hash = hash
                .as_str()
                .filter(|v| racp_contract::service_start::hash(v));
            Ok(Some((
                id.ok_or_else(|| RacpError::new("REQUEST_INVALID"))?,
                hash.ok_or_else(|| RacpError::new("REQUEST_INVALID"))?,
            )))
        }
        _ => Err(RacpError::new("REQUEST_INVALID")),
    }
}

/// Only a caller-selected, completed maintenance attempt can resume finalization.
/// Unknown effects (Claimed/Failed) never permit another worker or normal Run.
pub(super) fn resume_maintenance(
    record: Option<&racp_core::service_start::StartSnapshot>,
    purpose: racp_contract::service_start::MaintenancePurpose,
    cas: Option<(&str, &str)>,
) -> Result<bool, RacpError> {
    let Some(record) = record.filter(|record| !record.intent.permits_normal_run()) else {
        return if cas.is_none() {
            Ok(false)
        } else {
            Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"))
        };
    };
    if !matches!(&record.intent.command, StartCommand::Maintenance { purpose: actual, .. } if *actual == purpose)
        || !matches!(
            &record.intent.phase,
            racp_contract::service_start::StartPhase::Completed { .. }
        )
        || cas != Some((record.intent.request_id.as_str(), record.sha256.as_str()))
    {
        return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
    }
    Ok(true)
}

pub(super) fn maintenance_receipt(
    root: &Path,
    store: &StartStore,
    record: &racp_core::service_start::StartSnapshot,
    config_sha256: &str,
    device: &serde_json::Value,
) -> Result<serde_json::Value, RacpError> {
    let StartCommand::Maintenance { request_id, .. } = &record.intent.command else {
        return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
    };
    let raw = receipt_bytes(root, &record.intent.command)?;
    racp_contract::local_ipc::reject_duplicate_keys(&raw)?;
    let value: serde_json::Value = serde_json::from_slice(&raw)?;
    if value["format_version"] != 1
        || value["preflight_complete"] != true
        || value["request_id"] != *request_id
        || value["config_sha256"] != config_sha256
        || &value["device_id"] != device
        || completed(
            root,
            store,
            &record.intent.request_id,
            &record.intent.command,
        )?
        .is_none()
    {
        return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
    }
    Ok(value)
}

pub(super) fn open_service(root: &Path) -> Result<StartStore, RacpError> {
    racp_core::service_directory::require_service_actor()?;
    racp_core::verify_private_native_directory(root)?;
    StartStore::new(Workspaces::new(root, &[])?.directory("default", root)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use racp_contract::service_start::{MaintenancePurpose, StartIntent, StartPhase};
    use racp_core::service_start::StartSnapshot;

    #[test]
    fn completed_maintenance_rechecks_current_receipt_without_replaying_effects() {
        let root = std::env::temp_dir().join(racp_contract::new_id("racp_intent_test"));
        racp_core::create_private_native_directory(&root).unwrap();
        let store = StartStore::new(
            Workspaces::new(&root, &[])
                .unwrap()
                .directory("default", &root)
                .unwrap(),
        )
        .unwrap();
        let nonce = racp_contract::new_id("maintenance");
        let command = StartCommand::Maintenance {
            purpose: MaintenancePurpose::Remove,
            request_id: nonce.clone(),
        };
        let pending = store.prepare(command).unwrap();
        let config = "a".repeat(64);
        let device = serde_json::json!("device-fixture");
        let receipt = serde_json::to_vec(
            &serde_json::json!({"format_version":1,"preflight_complete":true,
            "request_id":nonce,"config_sha256":config,"device_id":device}),
        )
        .unwrap();
        let effects = std::sync::atomic::AtomicUsize::new(0);
        store
            .execute_once(&pending.intent.request_id, |command| {
                effects.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                racp_core::atomic_write(
                    &root.join(racp_runtime::service_maintenance::RESULT_FILE),
                    &receipt,
                    true,
                )?;
                receipt_bytes(&root, command)
            })
            .unwrap();
        let completed = store.snapshot().unwrap().unwrap();
        assert!(maintenance_receipt(&root, &store, &completed, &config, &device).is_ok());
        assert!(store.allow_run().is_err());
        assert!(
            maintenance_receipt(&root, &store, &completed, &config, &serde_json::Value::Null)
                .is_err()
        );
        assert!(store
            .execute_once(&pending.intent.request_id, |_| {
                effects.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                Ok(receipt.clone())
            })
            .is_err());
        racp_core::atomic_write(
            &root.join(racp_runtime::service_maintenance::RESULT_FILE),
            b"{}",
            true,
        )
        .unwrap();
        assert!(maintenance_receipt(&root, &store, &completed, &config, &device).is_err());
        assert_eq!(effects.load(std::sync::atomic::Ordering::SeqCst), 1);
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn maintenance_resumption_requires_exact_completed_record_and_purpose() {
        let mut record = StartSnapshot {
            intent: StartIntent {
                format_version: 1,
                root_identity: "1:2".into(),
                request_id: racp_contract::new_id("service_start"),
                command: StartCommand::Maintenance {
                    purpose: MaintenancePurpose::Restore,
                    request_id: racp_contract::new_id("maintenance"),
                },
                phase: StartPhase::Completed {
                    attempt_id: racp_contract::new_id("service_attempt"),
                    receipt_sha256: "a".repeat(64),
                },
            },
            sha256: "b".repeat(64),
        };
        let id = record.intent.request_id.clone();
        let hash = record.sha256.clone();
        let cas = Some((id.as_str(), hash.as_str()));
        assert!(resume_maintenance(Some(&record), MaintenancePurpose::Restore, cas).unwrap());
        assert!(resume_maintenance(Some(&record), MaintenancePurpose::Remove, cas).is_err());
        assert!(resume_maintenance(Some(&record), MaintenancePurpose::Restore, None).is_err());
        assert!(resume_maintenance(
            Some(&record),
            MaintenancePurpose::Restore,
            Some((&id, &"c".repeat(64)))
        )
        .is_err());
        record.intent.phase = StartPhase::Claimed {
            attempt_id: racp_contract::new_id("service_attempt"),
        };
        assert!(resume_maintenance(Some(&record), MaintenancePurpose::Restore, cas).is_err());
        record.intent.phase = StartPhase::Failed {
            attempt_id: racp_contract::new_id("service_attempt"),
            error_code: "CANCELLED".into(),
        };
        assert!(resume_maintenance(Some(&record), MaintenancePurpose::Restore, cas).is_err());
        record.intent.phase = StartPhase::Aborted {};
        assert!(!resume_maintenance(Some(&record), MaintenancePurpose::Restore, None).unwrap());
        assert!(resume_maintenance(Some(&record), MaintenancePurpose::Restore, cas).is_err());
        assert!(!resume_maintenance(None, MaintenancePurpose::Restore, None).unwrap());
        assert!(resume_maintenance(None, MaintenancePurpose::Restore, cas).is_err());
    }
}
pub(super) fn execute(
    root: &Path,
    request: &str,
    cancel: CancellationToken,
    ready: tokio::sync::oneshot::Sender<()>,
) -> Result<(), RacpError> {
    let store = open_service(root)?;
    store.execute_once(request, |command| {
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        let _ = ready.send(()); // The claim is durable before even announcing this one-shot start.
        match command {
            StartCommand::Configure { input_sha256 } => {
                racp_core::service_config::update(root, input_sha256)?
            }
            StartCommand::Reconcile {} => racp_core::service_config::reconcile(root)?,
            StartCommand::RecoverSessions { request_id } => {
                racp_runtime::providers::desktop::recover_service_sessions(
                    root, request_id, cancel,
                )?;
            }
            StartCommand::Maintenance { request_id, .. } => {
                racp_runtime::service_maintenance::inspect(root, request_id)?
            }
        }
        receipt_bytes(root, command)
    })
}
pub(super) fn receipt_bytes(root: &Path, command: &StartCommand) -> Result<Vec<u8>, RacpError> {
    let name = match command {
        StartCommand::Configure { .. } | StartCommand::Reconcile {} => {
            racp_core::service_config::RESULT_FILE
        }
        StartCommand::RecoverSessions { .. } => {
            racp_runtime::providers::desktop::SESSION_RECOVERY_FILE
        }
        StartCommand::Maintenance { .. } => racp_runtime::service_maintenance::RESULT_FILE,
    };
    racp_core::read_bounded(&root.join(name), 16384, false)
}

pub(super) fn completed(
    root: &Path,
    store: &StartStore,
    request: &str,
    command: &StartCommand,
) -> Result<Option<racp_core::service_start::StartSnapshot>, RacpError> {
    let record = store
        .snapshot()?
        .ok_or_else(|| RacpError::new("SERVICE_START_INTENT_CONFLICT"))?;
    if record.intent.request_id != request || &record.intent.command != command {
        return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
    }
    match &record.intent.phase {
        racp_contract::service_start::StartPhase::Completed { receipt_sha256, .. } => {
            if *receipt_sha256 != racp_contract::digest(receipt_bytes(root, command)?) {
                return Err(RacpError::new("SERVICE_START_RECEIPT_INVALID"));
            }
            Ok(Some(record))
        }
        racp_contract::service_start::StartPhase::Failed { .. } => {
            Err(RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"))
        }
        racp_contract::service_start::StartPhase::Prepared {}
        | racp_contract::service_start::StartPhase::Claimed { .. } => Ok(None),
        _ => Err(RacpError::new("SERVICE_START_INTENT_CONFLICT")),
    }
}
