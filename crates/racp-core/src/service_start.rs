//! Pinned intent storage. Callers first prove SYSTEM/service actor and private-root ACL.
use crate::{atomic_write, Directory, InstanceLock};
use racp_contract::{
    digest, local_ipc::reject_duplicate_keys, new_id, service_start::*, RacpError,
};
use serde::Serialize;
use std::io::Read;

pub const INTENT_FILE: &str = "native-service-start-intent.json";
pub const ADMIN_LOCK: &str = "native-service-admin.lock";
pub const EXECUTION_LOCK: &str = "native-service-execution.lock";
pub const INTENT_LOCK: &str = "native-service-start-intent.lock";
pub const MAINTENANCE_RECEIPT_FILE: &str = "native-service-maintenance.json";
const MAX_INTENT_BYTES: usize = 4096;
const MAX_RECEIPT_BYTES: usize = 16384;

#[derive(Clone, Debug, Serialize)]
pub struct StartSnapshot {
    pub intent: StartIntent,
    pub sha256: String,
}
pub struct StartStore {
    directory: Directory,
    root_identity: String,
}
impl StartStore {
    pub fn new(directory: Directory) -> Result<Self, RacpError> {
        let info = directory.own_info()?;
        let root_identity = format!("{:x}:{:x}", info.device, info.inode);
        Ok(Self {
            directory,
            root_identity,
        })
    }
    pub fn admin_guard(&self) -> Result<InstanceLock, RacpError> {
        InstanceLock::acquire(&self.directory.path.join(ADMIN_LOCK))
    }
    pub fn execution_guard(&self) -> Result<InstanceLock, RacpError> {
        InstanceLock::acquire(&self.directory.path.join(EXECUTION_LOCK))
    }
    fn lock(&self) -> Result<InstanceLock, RacpError> {
        InstanceLock::acquire(&self.directory.path.join(INTENT_LOCK))
    }
    fn read(&self) -> Result<Option<StartSnapshot>, RacpError> {
        if self.directory.info(INTENT_FILE)?.is_none() {
            return Ok(None);
        }
        let mut raw = vec![];
        self.directory
            .open_pinned_read(INTENT_FILE)?
            .take((MAX_INTENT_BYTES + 1) as u64)
            .read_to_end(&mut raw)?;
        if raw.len() > MAX_INTENT_BYTES {
            return Err(RacpError::new("SERVICE_START_INTENT_INVALID"));
        }
        reject_duplicate_keys(&raw).map_err(|_| RacpError::new("SERVICE_START_INTENT_INVALID"))?;
        let intent: StartIntent = serde_json::from_slice(&raw)
            .map_err(|_| RacpError::new("SERVICE_START_INTENT_INVALID"))?;
        intent.validate()?;
        if intent.root_identity != self.root_identity {
            return Err(RacpError::new("SERVICE_START_INTENT_INVALID"));
        }
        Ok(Some(StartSnapshot {
            intent,
            sha256: digest(&raw),
        }))
    }
    pub fn snapshot(&self) -> Result<Option<StartSnapshot>, RacpError> {
        let _lock = self.lock()?;
        self.read()
    }
    pub fn allow_run(&self) -> Result<(), RacpError> {
        if self
            .snapshot()?
            .is_some_and(|record| !record.intent.permits_normal_run())
        {
            return Err(RacpError::new("SERVICE_START_BLOCKED"));
        }
        Ok(())
    }
    fn write(&self, intent: StartIntent, overwrite: bool) -> Result<StartSnapshot, RacpError> {
        intent.validate()?;
        let raw = serde_json::to_vec(&intent)?;
        if raw.len() > MAX_INTENT_BYTES {
            return Err(RacpError::new("SERVICE_START_INTENT_INVALID"));
        }
        atomic_write(&self.directory.path.join(INTENT_FILE), &raw, overwrite)?;
        let written = self
            .read()?
            .ok_or_else(|| RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"))?;
        if written.sha256 != digest(raw) {
            return Err(RacpError::new("SERVICE_START_OUTCOME_UNKNOWN"));
        }
        Ok(written)
    }
    pub fn prepare(&self, command: StartCommand) -> Result<StartSnapshot, RacpError> {
        command.validate()?;
        let _lock = self.lock()?;
        let prior = self.read()?;
        if prior
            .as_ref()
            .is_some_and(|record| !record.intent.permits_normal_run())
        {
            return Err(RacpError::new("SERVICE_START_BLOCKED"));
        }
        self.write(
            StartIntent {
                format_version: 1,
                root_identity: self.root_identity.clone(),
                request_id: new_id("service_start"),
                command,
                phase: StartPhase::Prepared {},
            },
            prior.is_some(),
        )
    }
    fn cas(&self, request: &str, expected: &str) -> Result<StartSnapshot, RacpError> {
        let record = self
            .read()?
            .ok_or_else(|| RacpError::new("SERVICE_START_INTENT_CONFLICT"))?;
        if record.sha256 != expected || record.intent.request_id != request {
            return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
        }
        Ok(record)
    }
    fn claim(&self, request: &str) -> Result<StartSnapshot, RacpError> {
        let _lock = self.lock()?;
        let mut record = self
            .read()?
            .ok_or_else(|| RacpError::new("SERVICE_START_REPLAY"))?;
        if record.intent.request_id != request
            || !matches!(record.intent.phase, StartPhase::Prepared {})
        {
            return Err(RacpError::new("SERVICE_START_REPLAY"));
        }
        record.intent.phase = StartPhase::Claimed {
            attempt_id: new_id("service_attempt"),
        };
        self.write(record.intent, true)
    }
    pub fn execute_once(
        &self,
        request: &str,
        effect: impl FnOnce(&StartCommand) -> Result<Vec<u8>, RacpError>,
    ) -> Result<(), RacpError> {
        let claimed = self.claim(request)?;
        let outcome = effect(&claimed.intent.command).and_then(|receipt| receipt_hash(&receipt));
        let _lock = self.lock()?;
        let mut current = self.cas(request, &claimed.sha256)?;
        let StartPhase::Claimed { attempt_id } = current.intent.phase else {
            return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
        };
        match outcome {
            Ok(receipt_sha256) => {
                current.intent.phase = StartPhase::Completed {
                    attempt_id,
                    receipt_sha256,
                };
                self.write(current.intent, true)?;
                Ok(())
            }
            Err(error) => {
                let error_code = if error_code_valid(error.code.0) {
                    error.code.0
                } else {
                    "SERVICE_ADMIN_FAILED"
                };
                current.intent.phase = StartPhase::Failed {
                    attempt_id,
                    error_code: error_code.to_owned(),
                };
                self.write(current.intent, true)?;
                Err(RacpError::new(error_code))
            }
        }
    }
    pub fn acknowledge(
        &self,
        request: &str,
        expected: &str,
        receipt: &[u8],
    ) -> Result<StartSnapshot, RacpError> {
        let receipt_sha = receipt_hash(receipt)?;
        let _lock = self.lock()?;
        let mut record = self.cas(request, expected)?;
        let StartPhase::Completed {
            attempt_id,
            receipt_sha256,
        } = record.intent.phase
        else {
            return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
        };
        if receipt_sha256 != receipt_sha {
            return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
        }
        record.intent.phase = StartPhase::Acknowledged {
            attempt_id,
            receipt_sha256,
        };
        self.write(record.intent, true)
    }
    pub fn abort_prepared(
        &self,
        request: &str,
        expected: &str,
    ) -> Result<StartSnapshot, RacpError> {
        let _lock = self.lock()?;
        let mut record = self.cas(request, expected)?;
        if !matches!(record.intent.phase, StartPhase::Prepared {}) {
            return Err(RacpError::new("SERVICE_START_INTENT_CONFLICT"));
        }
        record.intent.phase = StartPhase::Aborted {};
        self.write(record.intent, true)
    }
}
fn receipt_hash(raw: &[u8]) -> Result<String, RacpError> {
    if raw.len() > MAX_RECEIPT_BYTES
        || reject_duplicate_keys(raw).is_err()
        || !serde_json::from_slice::<serde_json::Value>(raw).is_ok_and(|value| value.is_object())
    {
        return Err(RacpError::new("SERVICE_START_RECEIPT_INVALID"));
    }
    Ok(digest(raw))
}
