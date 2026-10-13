//! Pinned fresh-provision receipt CAS. Caller proves SYSTEM and parent ACL first.
use crate::{atomic_write, Directory, InstanceLock};
use racp_contract::{digest, local_ipc::reject_duplicate_keys, service_provision::*, RacpError};
use serde::Serialize;
use std::io::Read;

pub const RECEIPT_FILE: &str = "native-service-provision.json";
const RECEIPT_LOCK: &str = "native-service-provision.lock";
const MAX_BYTES: usize = 4096;
#[derive(Clone, Debug, Serialize)]
pub struct ProvisionSnapshot {
    pub receipt: ProvisionReceipt,
    pub sha256: String,
    pub root_identity: String,
}
pub struct ProvisionStore {
    directory: Directory,
    root_identity: String,
}
impl ProvisionStore {
    pub fn new(directory: Directory) -> Result<Self, RacpError> {
        let info = directory.own_info()?;
        Ok(Self {
            directory,
            root_identity: format!("{:x}:{:x}", info.device, info.inode),
        })
    }
    fn lock(&self) -> Result<InstanceLock, RacpError> {
        InstanceLock::acquire(&self.directory.path.join(RECEIPT_LOCK))
    }
    fn read(&self) -> Result<Option<ProvisionSnapshot>, RacpError> {
        if self.directory.info(RECEIPT_FILE)?.is_none() {
            return Ok(None);
        }
        let mut raw = vec![];
        self.directory
            .open_pinned_read(RECEIPT_FILE)?
            .take((MAX_BYTES + 1) as u64)
            .read_to_end(&mut raw)?;
        if raw.len() > MAX_BYTES {
            return Err(RacpError::new("SERVICE_PROVISION_RECEIPT_INVALID"));
        }
        reject_duplicate_keys(&raw)
            .map_err(|_| RacpError::new("SERVICE_PROVISION_RECEIPT_INVALID"))?;
        let receipt: ProvisionReceipt = serde_json::from_slice(&raw)
            .map_err(|_| RacpError::new("SERVICE_PROVISION_RECEIPT_INVALID"))?;
        receipt.validate()?;
        Ok(Some(ProvisionSnapshot {
            receipt,
            sha256: digest(raw),
            root_identity: self.root_identity.clone(),
        }))
    }
    pub fn snapshot(&self) -> Result<Option<ProvisionSnapshot>, RacpError> {
        let _lock = self.lock()?;
        self.read()
    }
    fn write(
        &self,
        receipt: ProvisionReceipt,
        overwrite: bool,
    ) -> Result<ProvisionSnapshot, RacpError> {
        receipt.validate()?;
        let raw = serde_json::to_vec(&receipt)?;
        atomic_write(&self.directory.path.join(RECEIPT_FILE), &raw, overwrite)?;
        let written = self
            .read()?
            .ok_or_else(|| RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"))?;
        if written.sha256 != digest(raw) {
            return Err(RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"));
        }
        Ok(written)
    }
    pub fn begin(&self, receipt: ProvisionReceipt) -> Result<ProvisionSnapshot, RacpError> {
        receipt.validate()?;
        let _lock = self.lock()?;
        if receipt.phase != ProvisionPhase::Reserved || self.read()?.is_some() {
            return Err(RacpError::new("SERVICE_PROVISION_CONFLICT"));
        }
        self.write(receipt, false)
    }
    pub fn advance(
        &self,
        expected: &ProvisionSnapshot,
        phase: ProvisionPhase,
    ) -> Result<ProvisionSnapshot, RacpError> {
        let _lock = self.lock()?;
        let current = self
            .read()?
            .ok_or_else(|| RacpError::new("SERVICE_PROVISION_CONFLICT"))?;
        if current.root_identity != expected.root_identity
            || current.sha256 != expected.sha256
            || current.receipt != expected.receipt
            || current.receipt.phase.next() != Some(phase)
        {
            return Err(RacpError::new("SERVICE_PROVISION_CONFLICT"));
        }
        let mut next = current.receipt;
        next.phase = phase;
        self.write(next, true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn setup(root: &std::path::Path) -> ProvisionStore {
        ProvisionStore::new(
            crate::Workspaces::new(root, &[])
                .unwrap()
                .directory("default", root)
                .unwrap(),
        )
        .unwrap()
    }
    fn receipt() -> ProvisionReceipt {
        ProvisionReceipt::reserved(
            racp_contract::new_id("provision"),
            "a".repeat(64),
            "b".repeat(64),
        )
        .unwrap()
    }
    #[test]
    fn copied_receipt_cannot_use_another_roots_snapshot_for_cas() {
        let first_root = tempfile::tempdir().unwrap();
        let second_root = tempfile::tempdir().unwrap();
        let first = setup(first_root.path()).begin(receipt()).unwrap();
        let raw = std::fs::read(first_root.path().join(RECEIPT_FILE)).unwrap();
        atomic_write(&second_root.path().join(RECEIPT_FILE), &raw, false).unwrap();
        let second_store = setup(second_root.path());
        assert!(second_store
            .advance(&first, ProvisionPhase::ServiceCreatedDisabled)
            .is_err());
        assert_eq!(
            std::fs::read(second_root.path().join(RECEIPT_FILE)).unwrap(),
            raw
        );
    }
    #[test]
    fn interrupted_steps_are_inspectable_and_stale_or_skipped_progress_is_rejected() {
        let root = tempfile::tempdir().unwrap();
        let store = setup(root.path());
        let first = store.begin(receipt()).unwrap();
        assert!(store.begin(receipt()).is_err());
        assert!(store
            .advance(&first, ProvisionPhase::InputsCommitted)
            .is_err());
        let second = store
            .advance(&first, ProvisionPhase::ServiceCreatedDisabled)
            .unwrap();
        assert!(store
            .advance(&first, ProvisionPhase::ServiceCreatedDisabled)
            .is_err());
        let snapshot = setup(root.path()).snapshot().unwrap().unwrap();
        assert_eq!(snapshot.sha256, second.sha256);
        assert!(!snapshot.receipt.ready && !snapshot.receipt.registered);
        let third = store
            .advance(&snapshot, ProvisionPhase::InputsCommitted)
            .unwrap();
        let fourth = store
            .advance(&third, ProvisionPhase::RecoveryCommitted)
            .unwrap();
        let final_record = store
            .advance(&fourth, ProvisionPhase::PreparedDemandStart)
            .unwrap();
        assert!(store
            .advance(&final_record, ProvisionPhase::Reserved)
            .is_err());
    }
    #[test]
    fn foreign_nonce_hash_changes_and_corrupt_receipts_cannot_be_overwritten() {
        let root = tempfile::tempdir().unwrap();
        let store = setup(root.path());
        let first = store.begin(receipt()).unwrap();
        let mut foreign = first.clone();
        foreign.receipt.nonce = racp_contract::new_id("provision");
        assert!(store
            .advance(&foreign, ProvisionPhase::ServiceCreatedDisabled)
            .is_err());
        foreign = first.clone();
        foreign.sha256 = "c".repeat(64);
        assert!(store
            .advance(&foreign, ProvisionPhase::ServiceCreatedDisabled)
            .is_err());
        let path = root.path().join(RECEIPT_FILE);
        for raw in [
            b"{\"phase\":\"reserved\",\"phase\":\"inputs_committed\"}".as_slice(),
            &[b'x'; MAX_BYTES + 1],
        ] {
            atomic_write(&path, raw, true).unwrap();
            assert!(store.snapshot().is_err());
            assert!(store
                .advance(&first, ProvisionPhase::ServiceCreatedDisabled)
                .is_err());
            assert_eq!(std::fs::read(&path).unwrap(), raw);
        }
    }
}
