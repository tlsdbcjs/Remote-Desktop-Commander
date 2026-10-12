use racp_core::{journal_schema, Journal, OutputSpool};

#[test]
fn journal_reopens_after_output_spool_initialization() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("execution.db");
    let journal = Journal::open(&path).unwrap();
    let spool = OutputSpool::new(temporary.path().join("outputs"), journal.clone()).unwrap();
    drop(spool);
    drop(journal);
    journal_schema::inspect_existing(&path).unwrap();
    let reopened = Journal::open(&path).unwrap();
    drop(reopened);
}

#[test]
fn journal_still_rejects_an_unrecognized_table() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("execution.db");
    drop(Journal::open(&path).unwrap());
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute_batch("CREATE TABLE foreign_state(value TEXT);")
        .unwrap();
    drop(db);
    assert_eq!(
        journal_schema::inspect_existing(&path).unwrap_err().code.0,
        "JOURNAL_SCHEMA_UNSUPPORTED"
    );
    assert!(Journal::open(&path).is_err());
}
