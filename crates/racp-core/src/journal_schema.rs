//! Native Rust Journal format. Unsupported or partial stores are never repaired on open.
use racp_contract::RacpError;
use rusqlite::{Connection, OpenFlags, TransactionBehavior};
use std::{collections::BTreeMap, path::Path, sync::LazyLock};

pub const VERSION: u32 = 1;
const SQL: &str = "
CREATE TABLE outcome_retention_clocks(id TEXT PRIMARY KEY,clock_id TEXT NOT NULL,committed_monotonic REAL NOT NULL);
CREATE TABLE operations(id TEXT PRIMARY KEY,scope TEXT NOT NULL,key_hash TEXT NOT NULL,payload_hash TEXT NOT NULL,device_id TEXT NOT NULL,request TEXT NOT NULL,state TEXT NOT NULL,result TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,outcome_available INTEGER NOT NULL DEFAULT 1,mutation INTEGER NOT NULL DEFAULT 1,retain_key INTEGER NOT NULL DEFAULT 1,publication_blocked INTEGER NOT NULL DEFAULT 0,UNIQUE(scope,key_hash));
CREATE TABLE audit(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,event TEXT NOT NULL,device_id TEXT NOT NULL,operation_id TEXT NOT NULL,operation TEXT NOT NULL,trace_id TEXT NOT NULL,request_id TEXT NOT NULL,summary TEXT NOT NULL,owner_id TEXT NOT NULL DEFAULT 'owner_local');
CREATE TABLE idempotency_tombstones(scope TEXT NOT NULL,key_hash TEXT NOT NULL,payload_hash TEXT NOT NULL,operation_id TEXT NOT NULL UNIQUE,device_id TEXT NOT NULL,outcome_available INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(scope,key_hash));
CREATE TABLE operation_resolutions(id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,result TEXT,error TEXT,observed_at TEXT NOT NULL,outcome_available INTEGER NOT NULL DEFAULT 1,UNIQUE(operation_id,digest));
CREATE INDEX operations_retention ON operations(outcome_available,state,updated_at);
CREATE INDEX operations_mutation_device ON operations(device_id,mutation);
CREATE INDEX retired_transient_outcomes ON audit(operation_id,device_id,request_id,trace_id) WHERE event='transient_outcome_retired';
CREATE TABLE execution_progress(operation_id TEXT PRIMARY KEY,state TEXT NOT NULL,waiting_reason TEXT,progress REAL,revision INTEGER NOT NULL);
CREATE TABLE output_spool(id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,filename TEXT NOT NULL,size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,media_type TEXT NOT NULL,artifact_id TEXT,transfer_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,error_code TEXT);
CREATE INDEX output_spool_operation ON output_spool(operation_id);
CREATE INDEX output_spool_pending ON output_spool(artifact_id,updated_at);
PRAGMA user_version=1;
";
fn unsupported(_: rusqlite::Error) -> RacpError {
    RacpError::new("JOURNAL_SCHEMA_UNSUPPORTED")
}
// Ignore formatting outside SQL literals, preserving the exact constraints/defaults/indexes.
fn normalize(sql: &str) -> String {
    let mut quote = None;
    sql.chars()
        .filter_map(|c| {
            if let Some(end) = quote {
                if c == end {
                    quote = None;
                }
                Some(c)
            } else if c == '\'' || c == '"' {
                quote = Some(c);
                Some(c)
            } else if c.is_ascii_whitespace() {
                None
            } else {
                Some(c.to_ascii_lowercase())
            }
        })
        .collect()
}
fn schema(db: &Connection) -> Result<BTreeMap<String, String>, RacpError> {
    let mut stmt = db.prepare("SELECT type,name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name LIMIT 65")
        .map_err(unsupported)?;
    let values = stmt
        .query_map([], |row| {
            let text = |index| -> rusqlite::Result<String> {
                let raw = row.get_ref(index)?.as_str()?;
                if raw.len() > 16384 {
                    return Err(rusqlite::Error::InvalidQuery);
                }
                Ok(raw.to_owned())
            };
            Ok((format!("{}:{}", text(0)?, text(1)?), normalize(&text(2)?)))
        })
        .map_err(unsupported)?
        .collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(unsupported)?;
    if values.len() > 64 {
        return Err(RacpError::new("JOURNAL_SCHEMA_UNSUPPORTED"));
    }
    Ok(values)
}
static EXPECTED: LazyLock<Result<BTreeMap<String, String>, RacpError>> = LazyLock::new(|| {
    // This is the runtime definition of the format, not a persisted fixture or migration store.
    let db = Connection::open_in_memory().map_err(unsupported)?;
    db.execute_batch(SQL).map_err(unsupported)?;
    schema(&db)
});
fn verify(db: &Connection) -> Result<(), RacpError> {
    let version: u32 = db
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(unsupported)?;
    let expected = EXPECTED.as_ref().map_err(|error| *error)?;
    if version != VERSION || &schema(db)? != expected {
        return Err(RacpError::new("JOURNAL_SCHEMA_UNSUPPORTED"));
    }
    Ok(())
}
pub(crate) fn initialize(db: &mut Connection) -> Result<(), RacpError> {
    let tx = db
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(unsupported)?;
    let version: u32 = tx
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(unsupported)?;
    if version == 0 {
        if !schema(&tx)?.is_empty() {
            return Err(RacpError::new("JOURNAL_SCHEMA_UNSUPPORTED"));
        }
        tx.execute_batch(SQL).map_err(unsupported)?;
    }
    verify(&tx)?;
    tx.commit().map_err(unsupported)
}
/// Maintenance inspection: no creation, ALTER, schema initialization, or user-data conversion.
pub fn inspect_existing(path: &Path) -> Result<(), RacpError> {
    crate::validate_local_path(path)?;
    let db = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(unsupported)?;
    db.busy_timeout(std::time::Duration::from_secs(5))
        .map_err(unsupported)?;
    let tx = db.unchecked_transaction().map_err(unsupported)?;
    verify(&tx)?;
    tx.commit().map_err(unsupported)
}
