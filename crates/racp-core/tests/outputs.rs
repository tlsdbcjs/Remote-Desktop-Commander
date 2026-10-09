use racp_contract::digest;
use racp_core::{Journal, OutputSpool};
use serde_json::json;

#[test]
fn durable_output_survives_reopen_and_never_overwrites_execution() {
    let root = tempfile::tempdir().unwrap();
    let db = root.path().join("execution.db");
    let spool = root.path().join("spool");
    let journal = Journal::open(&db).unwrap();
    let outputs = OutputSpool::new(spool.clone(), journal).unwrap();
    std::fs::write(spool.join("op_1.json"), b"result").unwrap();
    let output = outputs
        .register(&spool.join("op_1.json"), "op_1", "application/json")
        .unwrap();
    assert_eq!(output["sha256"], digest(b"result"));
    outputs
        .set_transfer(output["id"].as_str().unwrap(), "transfer_1")
        .unwrap();
    drop(outputs);
    let outputs = OutputSpool::new(spool.clone(), Journal::open(&db).unwrap()).unwrap();
    assert_eq!(outputs.pending().unwrap()[0]["transfer_id"], "transfer_1");
    outputs
        .completed(output["id"].as_str().unwrap(), "artifact_1")
        .unwrap();
    assert_eq!(
        outputs.descriptors("op_1").unwrap()[0]["artifact_id"],
        "artifact_1"
    );
    assert!(outputs.pending().unwrap().is_empty());
    outputs.collect_completed_files().unwrap();
    assert!(!spool.join("op_1.json").exists());
}

#[test]
fn spool_bounds_before_side_effect_and_cleanup_remains_possible() {
    let root = tempfile::tempdir().unwrap();
    let outputs = OutputSpool::new(
        root.path().join("spool"),
        Journal::open(&root.path().join("execution.db")).unwrap(),
    )
    .unwrap();
    for n in 0..9 {
        outputs
            .reserve(&format!("op_{n}"), "filesystem.read", &json!({}))
            .unwrap();
    }
    assert!(outputs
        .reserve(
            "op_large",
            "browser.upload",
            &json!({"artifact_id":"art_1"})
        )
        .is_err());
    outputs
        .reserve("op_cleanup", "process.terminate", &json!({}))
        .unwrap();
    outputs.release("op_0").unwrap();
    outputs
        .reserve("op_next", "filesystem.read", &json!({}))
        .unwrap();
    std::fs::write(root.path().join("outside"), b"secret").unwrap();
    assert!(outputs
        .register(&root.path().join("outside"), "op_1", "text/plain")
        .is_err());
}

#[test]
fn retained_browser_files_share_the_spool_admission_budget() {
    let root = tempfile::tempdir().unwrap();
    let outputs = OutputSpool::new(
        root.path().join("spool"),
        Journal::open(&root.path().join("execution.db")).unwrap(),
    )
    .unwrap();
    let uploads = root.path().join("browser/browser_one/uploads/upload_one");
    std::fs::create_dir_all(&uploads).unwrap();
    std::fs::write(
        uploads.join("retained.bin"),
        b"one byte crosses the shared limit",
    )
    .unwrap();
    for n in 0..9 {
        outputs
            .reserve(&format!("op_reserved_{n}"), "filesystem.read", &json!({}))
            .unwrap();
    }
    assert_eq!(
        outputs
            .reserve("op_new", "filesystem.read", &json!({}))
            .unwrap_err()
            .code
            .0,
        "RESOURCE_EXHAUSTED"
    );
    outputs
        .reserve("op_close", "browser.close", &json!({}))
        .unwrap();
}
