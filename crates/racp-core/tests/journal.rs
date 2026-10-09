use racp_core::{Acceptance, Journal};
use serde_json::{json, Value};
fn request() -> Value {
    json!({"protocol":1,"type":"request","device_id":"dev_1","agent_boot_id":"boot_1","connection_epoch":1,"request_id":"req_1","operation_id":"op_1","trace_id":"0".repeat(32),"timestamp":"2026-10-10T00:00:00Z","operation":"shell.exec","timeout_ms":60000,"remaining_timeout_ms":60000,"execution_mode":"sync","idempotency_key":"key","context":{"principal_id":"owner_local","workspace_id":"default","execution_profile_id":"trusted_personal","policy_revision":1},"payload":{"argv":["echo","한글"]},"retain_key":true})
}
#[test]
fn dedup_100() {
    let root = tempfile::tempdir().unwrap();
    let journal = Journal::open(&root.path().join("execution.db")).unwrap();
    let effects = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let workers: Vec<_> = (0..100)
        .map(|_| {
            let journal = journal.clone();
            let effects = effects.clone();
            std::thread::spawn(move || {
                if matches!(journal.accept(&request()).unwrap(), Acceptance::New(_)) {
                    effects.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                }
            })
        })
        .collect();
    for w in workers {
        w.join().unwrap();
    }
    assert_eq!(effects.load(std::sync::atomic::Ordering::SeqCst), 1);
    assert_eq!(journal.records().unwrap().len(), 1);
}
#[test]
fn payload_conflict() {
    let root = tempfile::tempdir().unwrap();
    let j = Journal::open(&root.path().join("execution.db")).unwrap();
    j.accept(&request()).unwrap();
    let mut different = request();
    different["payload"] = json!({"argv":["different"]});
    assert_eq!(
        j.accept(&different).err().unwrap().code.0,
        "IDEMPOTENCY_CONFLICT"
    );
}
#[test]
fn recover_unknown_and_late_result_is_separate() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("execution.db");
    let j = Journal::open(&path).unwrap();
    j.accept(&request()).unwrap();
    j.transition("op_1", "RUNNING", None, None).unwrap();
    drop(j);
    let j = Journal::open(&path).unwrap();
    j.recover_agent().unwrap();
    assert_eq!(j.get("op_1").unwrap().state, "UNKNOWN");
    j.transition("op_1", "SUCCEEDED", Some(json!({"exit_code":0})), None)
        .unwrap();
    assert_eq!(j.get("op_1").unwrap().state, "UNKNOWN");
    assert_eq!(j.resolutions("op_1").unwrap().len(), 1);
    assert!(matches!(
        j.accept(&request()).unwrap(),
        Acceptance::Replay(_)
    ));
}
#[test]
fn unsupported_schema_is_preserved() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("execution.db");
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute_batch("PRAGMA user_version=2;").unwrap();
    drop(db);
    assert!(Journal::open(&path).is_err());
    assert_eq!(
        rusqlite::Connection::open(&path)
            .unwrap()
            .query_row("PRAGMA user_version", [], |r| r.get::<_, u32>(0))
            .unwrap(),
        2
    );
}
#[test]
fn retention_utc_jump_cannot_retire_new_result() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("execution.db");
    let j = Journal::open(&path).unwrap();
    j.accept(&request()).unwrap();
    j.transition("op_1", "SUCCEEDED", Some(json!({"exit_code":0})), None)
        .unwrap();
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute(
        "UPDATE operations SET updated_at='2000-01-01T00:00:00Z'",
        [],
    )
    .unwrap();
    assert_eq!(j.compact(86400).unwrap()["outcomes_expired"], 0);
    assert!(j.get("op_1").unwrap().outcome_available);
    assert!(j.compact(1).is_err());
}
