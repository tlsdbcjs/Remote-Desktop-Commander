use racp_contract::{
    service_start::{StartCommand, StartPhase},
    RacpError,
};
use racp_core::{
    service_start::{StartStore, INTENT_FILE},
    Workspaces,
};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};

fn store(root: &std::path::Path) -> StartStore {
    racp_core::create_private_native_directory(root).unwrap();
    StartStore::new(
        Workspaces::new(root, &[])
            .unwrap()
            .directory("default", root)
            .unwrap(),
    )
    .unwrap()
}
fn command() -> StartCommand {
    StartCommand::Reconcile {}
}

#[test]
fn uncreated_restore_abort_requires_prepared_restore_and_exact_cas() {
    use racp_contract::service_start::MaintenancePurpose;
    let root = tempfile::tempdir().unwrap();
    let store = store(&root.path().join("private"));
    let restore = || StartCommand::Maintenance {
        purpose: MaintenancePurpose::Restore,
        request_id: racp_contract::new_id("maintenance"),
    };
    let prepared = store.prepare(restore()).unwrap();
    assert!(store
        .abort_prepared_restore(&prepared.intent.request_id, &"0".repeat(64))
        .is_err());
    assert!(store.allow_run().is_err());
    let aborted = store
        .abort_prepared_restore(&prepared.intent.request_id, &prepared.sha256)
        .unwrap();
    assert!(matches!(aborted.intent.phase, StartPhase::Aborted {}));
    assert!(store
        .execute_once(&prepared.intent.request_id, |_| panic!(
            "must never run aborted restore"
        ))
        .is_err());
    let next = store.prepare(restore()).unwrap();
    assert_ne!(next.intent.request_id, prepared.intent.request_id);
    store
        .execute_once(&next.intent.request_id, |_| Ok(b"{}".to_vec()))
        .unwrap();
    let completed = store.snapshot().unwrap().unwrap();
    assert!(store
        .abort_prepared_restore(&next.intent.request_id, &completed.sha256)
        .is_err());
    assert!(store.allow_run().is_err());
}

#[test]
fn uncreated_restore_abort_cannot_clear_a_different_prepared_command() {
    use racp_contract::service_start::MaintenancePurpose;
    let root = tempfile::tempdir().unwrap();
    let store = store(&root.path().join("private"));
    for command in [
        command(),
        StartCommand::Maintenance {
            purpose: MaintenancePurpose::Remove,
            request_id: racp_contract::new_id("maintenance"),
        },
    ] {
        let prepared = store.prepare(command).unwrap();
        assert!(store
            .abort_prepared_restore(&prepared.intent.request_id, &prepared.sha256)
            .is_err());
        assert_eq!(store.snapshot().unwrap().unwrap().sha256, prepared.sha256);
        store
            .abort_prepared(&prepared.intent.request_id, &prepared.sha256)
            .unwrap();
    }
}

#[test]
fn pending_and_claimed_admin_starts_block_normal_run_and_replay_after_crash() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path().join("private");
    let first = store(&root);
    let pending = first.prepare(command()).unwrap();
    assert!(first.allow_run().is_err());
    let effects = AtomicUsize::new(0);
    let crashed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = first.execute_once(&pending.intent.request_id, |_| {
            assert!(matches!(
                first.snapshot().unwrap().unwrap().intent.phase,
                StartPhase::Claimed { .. }
            ));
            effects.fetch_add(1, Ordering::SeqCst);
            panic!("owned fake admin worker crash");
        });
    }));
    assert!(crashed.is_err());
    let reopened = store(&root);
    assert!(reopened.allow_run().is_err());
    assert!(reopened
        .execute_once(&pending.intent.request_id, |_| {
            effects.fetch_add(1, Ordering::SeqCst);
            Ok(b"{}".to_vec())
        })
        .is_err());
    assert_eq!(effects.load(Ordering::SeqCst), 1);
}

#[test]
fn completed_receipt_requires_exact_cas_before_normal_start_is_admitted() {
    let root = tempfile::tempdir().unwrap();
    let store = store(&root.path().join("private"));
    let request = store.prepare(command()).unwrap();
    let receipt = b"{\"committed\":true}";
    store
        .execute_once(&request.intent.request_id, |_| Ok(receipt.to_vec()))
        .unwrap();
    let completed = store.snapshot().unwrap().unwrap();
    assert!(store.allow_run().is_err());
    assert!(store
        .acknowledge(&request.intent.request_id, &request.sha256, receipt)
        .is_err());
    assert!(store
        .acknowledge(&request.intent.request_id, &completed.sha256, b"{}")
        .is_err());
    store
        .acknowledge(&request.intent.request_id, &completed.sha256, receipt)
        .unwrap();
    store.allow_run().unwrap();
    assert!(store
        .execute_once(&request.intent.request_id, |_| panic!(
            "must not replay acknowledged start"
        ))
        .is_err());
    let next = store.prepare(command()).unwrap();
    assert_ne!(next.intent.request_id, request.intent.request_id);
}

#[test]
fn only_prepared_intent_can_be_explicitly_aborted_without_claiming_effects() {
    let root = tempfile::tempdir().unwrap();
    let store = store(&root.path().join("private"));
    let request = store.prepare(command()).unwrap();
    assert!(store
        .abort_prepared(&request.intent.request_id, &"0".repeat(64))
        .is_err());
    store
        .abort_prepared(&request.intent.request_id, &request.sha256)
        .unwrap();
    store.allow_run().unwrap();
    assert!(store
        .execute_once(&request.intent.request_id, |_| panic!(
            "aborted request replay"
        ))
        .is_err());
    let request = store.prepare(command()).unwrap();
    assert!(store
        .execute_once(&request.intent.request_id, |_| Err(RacpError::new(
            "SERVICE_CONFIG_OUTCOME_UNKNOWN"
        )))
        .is_err());
    let failed = store.snapshot().unwrap().unwrap();
    assert!(matches!(failed.intent.phase, StartPhase::Failed { .. }));
    assert!(store
        .abort_prepared(&request.intent.request_id, &failed.sha256)
        .is_err());
    assert!(store.allow_run().is_err());
}

#[test]
fn intents_are_bound_to_the_private_root_and_invalid_records_fail_closed() {
    let root = tempfile::tempdir().unwrap();
    let first_root = root.path().join("first");
    let other_root = root.path().join("other");
    let first = store(&first_root);
    first.prepare(command()).unwrap();
    let other = store(&other_root);
    std::fs::copy(first_root.join(INTENT_FILE), other_root.join(INTENT_FILE)).unwrap();
    assert!(other.snapshot().is_err());
    assert!(other.allow_run().is_err());
    std::fs::write(
        first_root.join(INTENT_FILE),
        b"{\"format_version\":1,\"format_version\":1}",
    )
    .unwrap();
    assert!(first.allow_run().is_err());
    std::fs::write(first_root.join(INTENT_FILE), vec![b'x'; 16385]).unwrap();
    assert!(first.allow_run().is_err());
}

#[test]
fn concurrent_admin_claims_have_only_one_actual_handler_effect() {
    let root = tempfile::tempdir().unwrap();
    let store = Arc::new(store(&root.path().join("private")));
    let request = store.prepare(command()).unwrap();
    let effects = Arc::new(AtomicUsize::new(0));
    let mut workers = vec![];
    for _ in 0..2 {
        let store = store.clone();
        let effects = effects.clone();
        let id = request.intent.request_id.clone();
        workers.push(std::thread::spawn(move || {
            store.execute_once(&id, |_| {
                effects.fetch_add(1, Ordering::SeqCst);
                Ok(b"{}".to_vec())
            })
        }));
    }
    let results: Vec<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(effects.load(Ordering::SeqCst), 1);
}

#[test]
fn delayed_old_start_cannot_claim_a_new_intent_with_the_same_command() {
    let root = tempfile::tempdir().unwrap();
    let store = store(&root.path().join("private"));
    let old = store.prepare(command()).unwrap();
    store
        .abort_prepared(&old.intent.request_id, &old.sha256)
        .unwrap();
    let next = store.prepare(command()).unwrap();
    assert!(store
        .execute_once(&old.intent.request_id, |_| panic!(
            "stale SDK start claimed new operation"
        ))
        .is_err());
    assert!(matches!(
        store.snapshot().unwrap().unwrap().intent.phase,
        StartPhase::Prepared {}
    ));
    store
        .execute_once(&next.intent.request_id, |_| Ok(b"{}".to_vec()))
        .unwrap();
}

#[test]
fn shared_execution_and_admin_locks_exclude_concurrent_admission_and_preparation() {
    let root = tempfile::tempdir().unwrap();
    let first = store(&root.path().join("private"));
    let other = store(&root.path().join("private"));
    let execution = first.execution_guard().unwrap();
    assert!(other.execution_guard().is_err());
    let admin = first.admin_guard().unwrap();
    assert!(other.admin_guard().is_err());
    drop(execution);
    drop(admin);
    other.execution_guard().unwrap();
    other.admin_guard().unwrap();
}
