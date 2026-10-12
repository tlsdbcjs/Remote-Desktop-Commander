use crate::{private_dir, validate_local_path};
use racp_contract::{canonical_digest, digest, new_id, registry, timestamp, RacpError};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{Arc, Mutex, MutexGuard},
    time::Instant,
};
const TERMINAL: &[&str] = &["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "UNKNOWN"];
const STATES: &[&str] = &[
    "ACCEPTED",
    "DISPATCHED",
    "RUNNING",
    "CANCEL_REQUESTED",
    "RECONCILING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
    "UNKNOWN",
];
#[derive(Clone)]
pub struct Journal {
    db: Arc<Mutex<Connection>>,
    clock: Arc<Clock>,
}
struct Clock {
    id: String,
    start: Instant,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Record {
    pub id: String,
    pub state: String,
    pub request: Value,
    pub result: Option<Value>,
    pub error: Option<Value>,
    pub outcome_available: bool,
    pub publication_blocked: bool,
}
pub enum Acceptance {
    New(Record),
    Replay(Record),
    Pending(Record),
}
impl Acceptance {
    pub fn record(&self) -> &Record {
        match self {
            Self::New(r) | Self::Replay(r) | Self::Pending(r) => r,
        }
    }
}
fn db_error(_: rusqlite::Error) -> RacpError {
    RacpError::new("LOCAL_STATE_FAILED")
}
fn row_record(row: &rusqlite::Row<'_>) -> rusqlite::Result<Record> {
    fn value(row: &rusqlite::Row<'_>, name: &str) -> rusqlite::Result<Option<Value>> {
        let s: Option<String> = row.get(name)?;
        s.map(|s| {
            serde_json::from_str(&s).map_err(|e| {
                rusqlite::Error::FromSqlConversionFailure(
                    0,
                    rusqlite::types::Type::Text,
                    Box::new(e),
                )
            })
        })
        .transpose()
    }
    Ok(Record {
        id: row.get("id")?,
        state: row.get("state")?,
        request: value(row, "request")?.unwrap_or(Value::Null),
        result: value(row, "result")?,
        error: value(row, "error")?,
        outcome_available: row.get("outcome_available")?,
        publication_blocked: row.get("publication_blocked")?,
    })
}
fn get(db: &Connection, id: &str) -> Result<Record, RacpError> {
    db.query_row("SELECT * FROM operations WHERE id=?", [id], row_record)
        .optional()
        .map_err(db_error)?
        .ok_or_else(|| RacpError::new("OPERATION_NOT_FOUND"))
}
fn audit(db: &Connection, event: &str, request: &Value, summary: Value) -> Result<(), RacpError> {
    let text = |k: &str| request[k].as_str().unwrap_or("").to_string();
    db.execute("INSERT INTO audit(id,timestamp,event,device_id,operation_id,operation,trace_id,request_id,summary,owner_id) VALUES(?,?,?,?,?,?,?,?,?,?)",params![new_id("aud"),timestamp(),event,text("device_id"),text("operation_id"),text("operation"),text("trace_id"),text("request_id"),summary.to_string(),request["context"]["principal_id"].as_str().unwrap_or("owner_local")]).map_err(db_error)?;
    Ok(())
}
impl Journal {
    pub fn open(path: &Path) -> Result<Self, RacpError> {
        validate_local_path(path)?;
        private_dir(
            path.parent()
                .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
        )?;
        let mut db = Connection::open(path).map_err(db_error)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
        }
        db.busy_timeout(std::time::Duration::from_secs(5))
            .map_err(db_error)?;
        crate::journal_schema::initialize(&mut db)?;
        db.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;",
        )
        .map_err(db_error)?;
        Ok(Self {
            db: Arc::new(Mutex::new(db)),
            clock: Arc::new(Clock {
                id: new_id("clock"),
                start: Instant::now(),
            }),
        })
    }
    pub(crate) fn connection(&self) -> Result<MutexGuard<'_, Connection>, RacpError> {
        self.db
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))
    }
    pub fn accept(&self, request: &Value) -> Result<Acceptance, RacpError> {
        let text = |k: &str| {
            request[k]
                .as_str()
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))
        };
        let owner = request["context"]["principal_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let scope = digest(format!("{}:{}", owner, text("device_id")?));
        let key = digest(text("idempotency_key")?);
        let mut context = request["context"].clone();
        if context["workspace_id"] == "default" {
            context.as_object_mut().unwrap().remove("workspace_id");
        }
        let payload = canonical_digest(
            &json!({"operation":request["operation"],"payload":request["payload"],"context":context,"timeout_ms":request["timeout_ms"],"execution_mode":request["execution_mode"]}),
        );
        let mut db = self.connection()?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        if let Some((id, hash)) = tx
            .query_row(
                "SELECT id,payload_hash FROM operations WHERE scope=? AND key_hash=?",
                params![scope, key],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
            )
            .optional()
            .map_err(db_error)?
        {
            if hash != payload {
                return Err(RacpError::new("IDEMPOTENCY_CONFLICT"));
            }
            let record = get(&tx, &id)?;
            if !record.outcome_available {
                return Err(RacpError::new("OPERATION_EXPIRED"));
            }
            tx.commit().map_err(db_error)?;
            return Ok(if TERMINAL.contains(&record.state.as_str()) {
                Acceptance::Replay(record)
            } else {
                Acceptance::Pending(record)
            });
        }
        let mutation = registry()[text("operation")?]["side_effect"]
            .as_bool()
            .unwrap_or(true);
        if mutation {
            let count: u64 = tx
                .query_row(
                    "SELECT COUNT(*) FROM operations WHERE device_id=? AND mutation=1",
                    [text("device_id")?],
                    |r| r.get(0),
                )
                .map_err(db_error)?;
            if count >= 1_000_000 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
        }
        tx.execute("INSERT INTO operations(id,scope,key_hash,payload_hash,device_id,request,state,created_at,updated_at,outcome_available,mutation,retain_key) VALUES(?,?,?,?,?,?,'ACCEPTED',?,?,1,?,?)",params![text("operation_id")?,scope,key,payload,text("device_id")?,request.to_string(),timestamp(),timestamp(),mutation,mutation||request["retain_key"].as_bool().unwrap_or(true)]).map_err(|_|RacpError::new("IDEMPOTENCY_CONFLICT"))?;
        audit(&tx, "accepted", request, json!({"payload_hash":payload}))?;
        let record = get(&tx, text("operation_id")?)?;
        tx.commit().map_err(db_error)?;
        Ok(Acceptance::New(record))
    }
    pub fn record_for_key(&self, request: &Value) -> Result<Record, RacpError> {
        let owner = request["context"]["principal_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let device = request["device_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let key = request["idempotency_key"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        self.connection()?
            .query_row(
                "SELECT * FROM operations WHERE scope=? AND key_hash=?",
                params![digest(format!("{owner}:{device}")), digest(key)],
                row_record,
            )
            .optional()
            .map_err(db_error)?
            .ok_or_else(|| RacpError::new("OPERATION_NOT_FOUND"))
    }
    pub fn get(&self, id: &str) -> Result<Record, RacpError> {
        get(&*self.connection()?, id)
    }
    /// Revoked output stays retired even if a later policy widens the grant again.
    pub fn block_publication(&self, id: &str) -> Result<(), RacpError> {
        let mut db = self.connection()?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let record = get(&tx, id)?;
        tx.execute(
            "UPDATE operations SET publication_blocked=1 WHERE id=?",
            [id],
        )
        .map_err(db_error)?;
        audit(
            &tx,
            "output_retired",
            &record.request,
            serde_json::json!({"publication_blocked":true}),
        )?;
        tx.commit().map_err(db_error)
    }
    pub fn records(&self) -> Result<Vec<Record>, RacpError> {
        let db = self.connection()?;
        let mut query = db
            .prepare("SELECT * FROM operations ORDER BY rowid")
            .map_err(db_error)?;
        let records = query
            .query_map([], row_record)
            .map_err(db_error)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(db_error)?;
        Ok(records)
    }
    pub fn transition(
        &self,
        id: &str,
        state: &str,
        result: Option<Value>,
        error: Option<Value>,
    ) -> Result<Record, RacpError> {
        if !STATES.contains(&state) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let mut db = self.connection()?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let record = get(&tx, id)?;
        if TERMINAL.contains(&record.state.as_str()) {
            if record.state == "UNKNOWN" && TERMINAL.contains(&state) && state != "UNKNOWN" {
                let content = json!({"state":state,"result":result,"error":error});
                let hash = resolution_digest(&content)?;
                let exists:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM operation_resolutions WHERE operation_id=? AND digest=?)",params![id,hash],|r|r.get(0)).map_err(db_error)?;
                if !exists {
                    let count: u32 = tx
                        .query_row(
                            "SELECT COUNT(*) FROM operation_resolutions WHERE operation_id=?",
                            [id],
                            |r| r.get(0),
                        )
                        .map_err(db_error)?;
                    if count >= 16 {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    let resolution = new_id("resolution");
                    tx.execute(
                        "INSERT INTO operation_resolutions VALUES(?,?,?,?,?,?,?,1)",
                        params![
                            resolution,
                            id,
                            hash,
                            state,
                            result.as_ref().map(Value::to_string),
                            error.as_ref().map(Value::to_string),
                            timestamp()
                        ],
                    )
                    .map_err(db_error)?;
                    tx.execute(
                        "INSERT INTO outcome_retention_clocks VALUES(?,?,?)",
                        params![
                            resolution,
                            self.clock.id,
                            self.clock.start.elapsed().as_secs_f64()
                        ],
                    )
                    .map_err(db_error)?;
                    audit(
                        &tx,
                        "late_result_recorded",
                        &record.request,
                        json!({"resolution_id":resolution,"reported_state":state}),
                    )?;
                }
            }
            tx.commit().map_err(db_error)?;
            return Ok(record);
        }
        tx.execute(
            "UPDATE operations SET state=?,result=?,error=?,updated_at=? WHERE id=?",
            params![
                state,
                result.as_ref().map(Value::to_string),
                error.as_ref().map(Value::to_string),
                timestamp(),
                id
            ],
        )
        .map_err(db_error)?;
        if TERMINAL.contains(&state) {
            tx.execute(
                "INSERT OR REPLACE INTO outcome_retention_clocks VALUES(?,?,?)",
                params![id, self.clock.id, self.clock.start.elapsed().as_secs_f64()],
            )
            .map_err(db_error)?;
        }
        audit(
            &tx,
            "state_changed",
            &record.request,
            json!({"state":state}),
        )?;
        let next = get(&tx, id)?;
        tx.commit().map_err(db_error)?;
        Ok(next)
    }
    /// Cancellation must not replace a terminal outcome when execution wins the race.
    pub fn request_cancel(&self, id: &str) -> Result<Record, RacpError> {
        let mut db = self.connection()?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let record = get(&tx, id)?;
        if !Self::terminal(&record.state) {
            tx.execute(
                "UPDATE operations SET state='CANCEL_REQUESTED',updated_at=? WHERE id=?",
                params![timestamp(), id],
            )
            .map_err(db_error)?;
            audit(
                &tx,
                "state_changed",
                &record.request,
                json!({"state":"CANCEL_REQUESTED"}),
            )?;
        }
        let result = get(&tx, id)?;
        tx.commit().map_err(db_error)?;
        Ok(result)
    }
    /// Metadata only for the native owner's UI; never includes RPC payload or outcomes.
    pub fn recent_activity(
        &self,
        device_id: &str,
        limit: usize,
    ) -> Result<Vec<racp_contract::local_ipc::ActivitySummary>, RacpError> {
        if !(1..=32).contains(&limit) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let db = self.connection()?;
        let mut query = db.prepare("SELECT id,json_extract(request,'$.request_id'),json_extract(request,'$.operation'),state FROM operations WHERE device_id=? ORDER BY rowid DESC LIMIT ?").map_err(db_error)?;
        let mut activity = query
            .query_map(params![device_id, limit as i64], |row| {
                Ok(racp_contract::local_ipc::ActivitySummary {
                    operation_id: row.get(0)?,
                    request_id: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    operation: row.get::<_, Option<String>>(2)?.unwrap_or_default(),
                    state: row.get(3)?,
                })
            })
            .map_err(db_error)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(db_error)?;
        activity.reverse();
        Ok(activity)
    }
    /// Persist intent before signaling an execution. This does not assert cancellation
    /// or cleanup succeeded; the execution's later terminal transition is authoritative.
    pub fn local_cancel_intent(&self, id: &str, receipt_id: &str) -> Result<(), RacpError> {
        if receipt_id.is_empty()
            || receipt_id.len() > 128
            || !receipt_id
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
        {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let mut db = self.connection()?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let record = get(&tx, id)?;
        audit(
            &tx,
            "local_cancel_intent",
            &record.request,
            json!({"receipt_id":receipt_id,"source":"local_owner","observed_state":record.state}),
        )?;
        tx.commit().map_err(db_error)?;
        Ok(())
    }
    pub fn recover_agent(&self) -> Result<(), RacpError> {
        for record in self.records()? {
            if !TERMINAL.contains(&record.state.as_str()) && record.outcome_available {
                self.transition(
                    &record.id,
                    "UNKNOWN",
                    None,
                    Some(error_value(
                        "EXECUTION_UNKNOWN",
                        "agent restarted before durable result",
                        "unknown",
                    )),
                )?;
            }
        }
        Ok(())
    }
    pub fn resolutions(&self, id: &str) -> Result<Vec<Value>, RacpError> {
        let db = self.connection()?;
        let mut query = db
            .prepare(
                "SELECT * FROM operation_resolutions WHERE operation_id=? ORDER BY observed_at,id",
            )
            .map_err(db_error)?;
        let rows=query.query_map([id],|r|{
   let result:Option<String>=r.get("result")?;let error:Option<String>=r.get("error")?;
   Ok(json!({"id":r.get::<_,String>("id")?,"operation_id":id,"digest":r.get::<_,String>("digest")?,"state":r.get::<_,String>("state")?,"result":result.and_then(|s|serde_json::from_str::<Value>(&s).ok()),"error":error.and_then(|s|serde_json::from_str::<Value>(&s).ok()),"observed_at":r.get::<_,String>("observed_at")?,"outcome_available":r.get::<_,bool>("outcome_available")?}))
  }).map_err(db_error)?.collect::<Result<Vec<_>,_>>().map_err(db_error)?;
        Ok(rows)
    }
    pub fn progress(
        &self,
        request: &Value,
        state: &str,
        waiting: Option<&str>,
    ) -> Result<u64, RacpError> {
        let mut db = self.connection()?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let id = request["operation_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let revision: u64 = tx
            .query_row(
                "SELECT revision FROM execution_progress WHERE operation_id=?",
                [id],
                |r| r.get(0),
            )
            .optional()
            .map_err(db_error)?
            .unwrap_or(0)
            + 1;
        tx.execute("INSERT INTO execution_progress VALUES(?,?,?,NULL,?) ON CONFLICT(operation_id) DO UPDATE SET state=excluded.state,waiting_reason=excluded.waiting_reason,revision=excluded.revision",params![id,state,waiting,revision]).map_err(db_error)?;
        audit(
            &tx,
            "job_state_changed",
            request,
            json!({"state":state,"waiting_reason":waiting}),
        )?;
        tx.commit().map_err(db_error)?;
        Ok(revision)
    }
    pub fn get_progress(&self, id: &str) -> Result<Value, RacpError> {
        Ok(self.connection()?.query_row("SELECT * FROM execution_progress WHERE operation_id=?",[id],|r|Ok(json!({"state":r.get::<_,String>("state")?,"waiting_reason":r.get::<_,Option<String>>("waiting_reason")?,"progress":r.get::<_,Option<f64>>("progress")?,"revision":r.get::<_,u64>("revision")?}))).optional().map_err(db_error)?.unwrap_or(Value::Null))
    }
    pub fn compact(&self, retention_seconds: u64) -> Result<Value, RacpError> {
        self.compact_pinned(retention_seconds, &std::collections::BTreeSet::new())
    }
    pub fn compact_pinned(
        &self,
        retention_seconds: u64,
        pinned: &std::collections::BTreeSet<String>,
    ) -> Result<Value, RacpError> {
        if retention_seconds < 86400 {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let cutoff = (chrono::Utc::now() - chrono::Duration::seconds(retention_seconds as i64))
            .to_rfc3339_opts(chrono::SecondsFormat::Micros, true);
        let mut db = self.connection()?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let mut expired = 0;
        let mut removed = 0;
        let ids: Vec<String> = {
            let mut query=tx.prepare("SELECT id FROM operations WHERE outcome_available=1 AND state IN('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','UNKNOWN') AND updated_at<=? ORDER BY rowid LIMIT 1000").map_err(db_error)?;
            let rows = query
                .query_map([&cutoff], |r| r.get(0))
                .map_err(db_error)?
                .collect::<Result<_, _>>()
                .map_err(db_error)?;
            rows
        };
        let has_spool:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='output_spool')",[],|r|r.get(0)).map_err(db_error)?;
        for id in ids {
            if pinned.contains(&id) {
                continue;
            }
            if has_spool && tx.query_row("SELECT EXISTS(SELECT 1 FROM output_spool WHERE operation_id=? AND artifact_id IS NULL)",[&id],|r|r.get::<_,bool>(0)).map_err(db_error)? {continue;}
            let current = self.clock.start.elapsed().as_secs_f64();
            let clock: Option<(String, f64)> = tx
                .query_row(
                    "SELECT clock_id,committed_monotonic FROM outcome_retention_clocks WHERE id=?",
                    [&id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()
                .map_err(db_error)?;
            if !clock
                .as_ref()
                .is_some_and(|(c, t)| c == &self.clock.id && current >= *t)
            {
                tx.execute(
                    "INSERT OR REPLACE INTO outcome_retention_clocks VALUES(?,?,?)",
                    params![id, self.clock.id, current],
                )
                .map_err(db_error)?;
                continue;
            }
            if current - clock.unwrap().1 < retention_seconds as f64 {
                continue;
            }
            let record = get(&tx, &id)?;
            let(mutation,retain,scope,key,hash,device):(bool,bool,String,String,String,String)=tx.query_row("SELECT mutation,retain_key,scope,key_hash,payload_hash,device_id FROM operations WHERE id=?",[&id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?))).map_err(db_error)?;
            if !mutation && !retain {
                audit(
                    &tx,
                    "transient_outcome_retired",
                    &record.request,
                    json!({"state":record.state}),
                )?;
                tx.execute("DELETE FROM operations WHERE id=?", [&id])
                    .map_err(db_error)?;
                removed += 1;
            } else {
                if mutation {
                    tx.execute(
                        "INSERT OR IGNORE INTO idempotency_tombstones VALUES(?,?,?,?,?,0)",
                        params![scope, key, hash, id, device],
                    )
                    .map_err(db_error)?;
                }
                let mut metadata = record.request;
                metadata.as_object_mut().unwrap().remove("payload");
                metadata.as_object_mut().unwrap().remove("idempotency_key");
                tx.execute("UPDATE operations SET request=?,result=NULL,error=NULL,outcome_available=0 WHERE id=?",params![metadata.to_string(),id]).map_err(db_error)?;
                audit(
                    &tx,
                    "outcome_expired",
                    &metadata,
                    json!({"mutation":mutation}),
                )?;
                expired += 1;
            }
            tx.execute("DELETE FROM outcome_retention_clocks WHERE id=?", [&id])
                .map_err(db_error)?;
        }
        let observations: Vec<String> = {
            let mut query=tx.prepare("SELECT id FROM operation_resolutions WHERE outcome_available=1 AND observed_at<=? LIMIT 1000").map_err(db_error)?;
            let items = query
                .query_map([&cutoff], |r| r.get(0))
                .map_err(db_error)?
                .collect::<Result<_, _>>()
                .map_err(db_error)?;
            items
        };
        for id in observations {
            let current = self.clock.start.elapsed().as_secs_f64();
            let clock: Option<(String, f64)> = tx
                .query_row(
                    "SELECT clock_id,committed_monotonic FROM outcome_retention_clocks WHERE id=?",
                    [&id],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()
                .map_err(db_error)?;
            if !clock
                .as_ref()
                .is_some_and(|(c, t)| c == &self.clock.id && current >= *t)
            {
                tx.execute(
                    "INSERT OR REPLACE INTO outcome_retention_clocks VALUES(?,?,?)",
                    params![id, self.clock.id, current],
                )
                .map_err(db_error)?;
                continue;
            }
            if current - clock.unwrap().1 < retention_seconds as f64 {
                continue;
            }
            tx.execute("UPDATE operation_resolutions SET result=NULL,error=NULL,outcome_available=0 WHERE id=?",[&id]).map_err(db_error)?;
            tx.execute("DELETE FROM outcome_retention_clocks WHERE id=?", [&id])
                .map_err(db_error)?;
        }
        let audit_cutoff = (chrono::Utc::now() - chrono::Duration::days(30))
            .to_rfc3339_opts(chrono::SecondsFormat::Micros, true);
        tx.execute(
            "DELETE FROM audit WHERE id IN (SELECT id FROM audit WHERE timestamp<? LIMIT 1000)",
            [audit_cutoff],
        )
        .map_err(db_error)?;
        tx.commit().map_err(db_error)?;
        Ok(json!({"outcomes_expired":expired,"ephemeral_reads_removed":removed}))
    }
    pub fn terminal(state: &str) -> bool {
        TERMINAL.contains(&state)
    }
}
pub fn error_value(code: &str, message: &str, execution_state: &str) -> Value {
    json!({"code":code,"message":message,"layer":"agent","retryable":false,"execution_state":execution_state,"details":{}})
}

// Existing Python resolution rows hash JSON with a space after commas/colons.
fn resolution_digest(value: &Value) -> Result<String, RacpError> {
    struct Formatter;
    impl serde_json::ser::Formatter for Formatter {
        fn begin_array_value<W: std::io::Write + ?Sized>(
            &mut self,
            writer: &mut W,
            first: bool,
        ) -> std::io::Result<()> {
            if !first {
                writer.write_all(b", ")?;
            }
            Ok(())
        }
        fn begin_object_key<W: std::io::Write + ?Sized>(
            &mut self,
            writer: &mut W,
            first: bool,
        ) -> std::io::Result<()> {
            if !first {
                writer.write_all(b", ")?;
            }
            Ok(())
        }
        fn begin_object_value<W: std::io::Write + ?Sized>(
            &mut self,
            writer: &mut W,
        ) -> std::io::Result<()> {
            writer.write_all(b": ")
        }
    }
    let mut bytes = vec![];
    value.serialize(&mut serde_json::Serializer::with_formatter(
        &mut bytes, Formatter,
    ))?;
    Ok(digest(bytes))
}
