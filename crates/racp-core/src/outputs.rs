//! Durable attachments are independent of the immutable execution outcome.
use crate::{
    paths::{OpenMode, Parent},
    private_dir, validate_local_path, Journal,
};
use racp_contract::{new_id, timestamp, RacpError};
use rusqlite::{params, OptionalExtension};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

#[derive(Clone)]
pub struct OutputSpool {
    root: PathBuf,
    journal: Journal,
    reservations: Arc<Mutex<BTreeMap<String, u64>>>,
}
fn db_error(_: rusqlite::Error) -> RacpError {
    RacpError::new("LOCAL_STATE_FAILED")
}
fn row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    Ok(
        json!({"id":row.get::<_,String>("id")?,"operation_id":row.get::<_,String>("operation_id")?,"filename":row.get::<_,String>("filename")?,"size_bytes":row.get::<_,u64>("size_bytes")?,"sha256":row.get::<_,String>("sha256")?,"media_type":row.get::<_,String>("media_type")?,"artifact_id":row.get::<_,Option<String>>("artifact_id")?,"transfer_id":row.get::<_,Option<String>>("transfer_id")?,"created_at":row.get::<_,String>("created_at")?,"updated_at":row.get::<_,String>("updated_at")?,"attempts":row.get::<_,u32>("attempts")?,"error_code":row.get::<_,Option<String>>("error_code")?}),
    )
}
impl OutputSpool {
    pub fn new(root: PathBuf, journal: Journal) -> Result<Self, RacpError> {
        private_dir(&root)?;
        journal.connection()?.execute_batch("CREATE TABLE IF NOT EXISTS output_spool(id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,filename TEXT NOT NULL,size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,media_type TEXT NOT NULL,artifact_id TEXT,transfer_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,error_code TEXT); CREATE INDEX IF NOT EXISTS output_spool_operation ON output_spool(operation_id); CREATE INDEX IF NOT EXISTS output_spool_pending ON output_spool(artifact_id,updated_at);").map_err(db_error)?;
        Ok(Self {
            root: validate_local_path(&root)?,
            journal,
            reservations: Arc::new(Mutex::new(BTreeMap::new())),
        })
    }
    pub fn root(&self) -> &Path {
        &self.root
    }
    pub fn reserve(
        &self,
        operation_id: &str,
        operation: &str,
        payload: &Value,
    ) -> Result<(), RacpError> {
        if [
            "terminal.close",
            "process.terminate",
            "browser.close",
            "browser.close_page",
            "desktop.lease_release",
        ]
        .contains(&operation)
        {
            return Ok(());
        }
        let mut reservations = self
            .reservations
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let pending: u64 = self
            .journal
            .connection()?
            .query_row(
                "SELECT COUNT(*) FROM output_spool WHERE artifact_id IS NULL",
                [],
                |r| r.get(0),
            )
            .map_err(db_error)?;
        if pending + reservations.len() as u64 >= 64 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let mut amount = match operation {
            "shell.exec" => 72 * 1024 * 1024,
            "filesystem.read" => 1024 * 1024 * 1024,
            "browser.download" => 2 * payload["max_bytes"].as_u64().unwrap_or(1024 * 1024 * 1024),
            "browser.upload" => 1024 * 1024 * 1024 + 64 * 1024 * 1024,
            _ => 64 * 1024 * 1024,
        };
        if payload["artifact_id"].is_string() {
            amount += 1024 * 1024 * 1024;
        }
        let mut used = 0u64;
        for entry in std::fs::read_dir(&self.root)? {
            let entry = entry?;
            validate_local_path(&entry.path())?;
            let info = entry.metadata()?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if info.is_file() && !reservations.contains_key(name.split('.').next().unwrap_or("")) {
                used = used.saturating_add(info.len());
            }
        }
        if used
            .saturating_add(reservations.values().sum::<u64>())
            .saturating_add(amount)
            > 10 * 1024 * 1024 * 1024
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        reservations.insert(operation_id.into(), amount);
        Ok(())
    }
    pub fn release(&self, id: &str) -> Result<(), RacpError> {
        self.reservations
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .remove(id);
        Ok(())
    }
    pub fn register(
        &self,
        path: &Path,
        operation_id: &str,
        media_type: &str,
    ) -> Result<Value, RacpError> {
        let path = validate_local_path(path)?;
        if path.parent() != Some(self.root.as_path())
            || ![
                "application/octet-stream",
                "application/json",
                "application/vnd.racp.output-stream",
                "image/png",
                "image/jpeg",
                "text/plain",
            ]
            .contains(&media_type)
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let parent = Parent::open(&self.root)?;
        let name = path
            .file_name()
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        let mut file = parent.file(name, OpenMode::Read)?;
        let mut hasher = Sha256::new();
        let mut size = 0u64;
        let mut buffer = [0u8; 65536];
        loop {
            let n = file.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            size += n as u64;
            if size > 1024 * 1024 * 1024 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            hasher.update(&buffer[..n]);
        }
        let hash = format!("{:x}", hasher.finalize());
        let id = new_id("output");
        let now = timestamp();
        let db = self.journal.connection()?;
        let count: u32 = db
            .query_row(
                "SELECT COUNT(*) FROM output_spool WHERE operation_id=?",
                [operation_id],
                |r| r.get(0),
            )
            .map_err(db_error)?;
        if count >= 4 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        db.execute(
            "INSERT INTO output_spool VALUES(?,?,?,?,?,?,NULL,NULL,?,?,0,NULL)",
            params![
                id,
                operation_id,
                name.to_string_lossy(),
                size,
                hash,
                media_type,
                now,
                now
            ],
        )
        .map_err(db_error)?;
        db.query_row("SELECT * FROM output_spool WHERE id=?", [id], row)
            .map_err(db_error)
    }
    pub fn get(&self, id: &str) -> Result<Value, RacpError> {
        self.journal
            .connection()?
            .query_row("SELECT * FROM output_spool WHERE id=?", [id], row)
            .optional()
            .map_err(db_error)?
            .ok_or_else(|| RacpError::new("ARTIFACT_NOT_FOUND"))
    }
    pub fn descriptors(&self, id: &str) -> Result<Vec<Value>, RacpError> {
        let db = self.journal.connection()?;
        let mut query = db
            .prepare("SELECT * FROM output_spool WHERE operation_id=? ORDER BY created_at LIMIT 4")
            .map_err(db_error)?;
        let items=query.query_map([id],|r|{let raw=row(r)?;Ok(json!({"id":raw["id"],"size_bytes":raw["size_bytes"],"sha256":raw["sha256"],"media_type":raw["media_type"],"artifact_id":raw["artifact_id"]}))}).map_err(db_error)?.collect::<Result<Vec<_>,_>>().map_err(db_error)?;
        Ok(items)
    }
    pub fn pending(&self) -> Result<Vec<Value>, RacpError> {
        let db = self.journal.connection()?;
        let mut query = db
            .prepare(
                "SELECT * FROM output_spool WHERE artifact_id IS NULL ORDER BY updated_at LIMIT 16",
            )
            .map_err(db_error)?;
        let items = query
            .query_map([], row)
            .map_err(db_error)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(db_error)?;
        Ok(items)
    }
    pub fn set_transfer(&self, id: &str, transfer: &str) -> Result<(), RacpError> {
        self.journal
            .connection()?
            .execute(
                "UPDATE output_spool SET transfer_id=? WHERE id=?",
                params![transfer, id],
            )
            .map_err(db_error)?;
        Ok(())
    }
    pub fn failed(&self, id: &str, code: &str) -> Result<(), RacpError> {
        self.journal.connection()?.execute("UPDATE output_spool SET attempts=attempts+1,error_code=?,updated_at=?,transfer_id=CASE WHEN ?='ARTIFACT_EXPIRED' THEN NULL ELSE transfer_id END WHERE id=?",params![code,timestamp(),code,id]).map_err(db_error)?;
        Ok(())
    }
    pub fn completed(&self, id: &str, artifact: &str) -> Result<(), RacpError> {
        self.journal
            .connection()?
            .execute(
                "UPDATE output_spool SET artifact_id=?,error_code=NULL,updated_at=? WHERE id=?",
                params![artifact, timestamp(), id],
            )
            .map_err(db_error)?;
        Ok(())
    }
    pub fn path(&self, output: &Value) -> Result<PathBuf, RacpError> {
        let filename = output["filename"]
            .as_str()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        if filename.contains(['/', '\\'])
            || filename == "."
            || filename == ".."
            || filename.contains('\0')
        {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        validate_local_path(&self.root.join(filename))
    }
    pub fn collect_completed_files(&self) -> Result<(), RacpError> {
        let items: Vec<Value> = {
            let db = self.journal.connection()?;
            let mut query = db
                .prepare("SELECT * FROM output_spool WHERE artifact_id IS NOT NULL")
                .map_err(db_error)?;
            let items = query
                .query_map([], row)
                .map_err(db_error)?
                .collect::<Result<_, _>>()
                .map_err(db_error)?;
            items
        };
        let parent = Parent::open(&self.root)?;
        for item in items {
            let path = self.path(&item)?;
            if path.try_exists()? {
                parent.remove(
                    path.file_name()
                        .unwrap()
                        .to_str()
                        .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
                )?;
            }
        }
        Ok(())
    }
}
