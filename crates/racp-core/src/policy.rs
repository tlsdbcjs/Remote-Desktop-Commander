//! Agent-owned policy; publication occurs only after a durable, cross-process CAS commit.
use crate::{
    atomic_write, read_bounded,
    state_schema::{load_policy, NativePolicy, POLICY_FILE},
    InstanceLock,
};
use racp_contract::{
    local_ipc::{reject_duplicate_keys, PolicyChanges},
    RacpError,
};
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex, RwLock},
};

pub type PolicySnapshot = NativePolicy;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PolicyReceipt {
    pub revision: u64,
    pub committed: bool,
    pub retirement_state: String,
    pub failed_resource_ids: Vec<String>,
}
#[derive(Clone)]
pub struct PolicyStore {
    root: PathBuf,
    snapshot: Arc<RwLock<PolicyCache>>,
    updates: Arc<Mutex<()>>,
}
struct PolicyCache {
    snapshot: PolicySnapshot,
    permissions: serde_json::Value,
}
impl PolicyStore {
    pub fn open(root: &Path) -> Result<Self, RacpError> {
        let snapshot = load_policy(root)?;
        let permissions = snapshot.permissions()?;
        Ok(Self {
            root: root.to_owned(),
            updates: Arc::new(Mutex::new(())),
            snapshot: Arc::new(RwLock::new(PolicyCache {
                snapshot,
                permissions,
            })),
        })
    }
    pub fn snapshot(&self) -> Result<PolicySnapshot, RacpError> {
        self.snapshot
            .read()
            .map(|s| s.snapshot.clone())
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))
    }
    pub fn permissions(&self) -> Result<(u64, serde_json::Value), RacpError> {
        self.snapshot
            .read()
            .map(|s| (s.snapshot.revision, s.permissions.clone()))
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))
    }
    /// Run on a bounded blocking worker; no gateway/credential fields are accepted here.
    pub fn update(
        &self,
        expected_revision: u64,
        changes: PolicyChanges,
    ) -> Result<PolicySnapshot, RacpError> {
        let _serial = self
            .updates
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let current = self.snapshot()?;
        if current.revision != expected_revision {
            return Err(RacpError::new("REVISION_CONFLICT"));
        }
        let _lock = InstanceLock::acquire(&self.root.join("native-policy.lock"))?;
        let raw = read_bounded(&self.root.join(POLICY_FILE), 65_536, false)?;
        reject_duplicate_keys(&raw)?;
        let durable: NativePolicy = serde_json::from_slice(&raw)?;
        if serde_json::to_value(&durable)? != serde_json::to_value(&current)? {
            return Err(RacpError::new("REVISION_CONFLICT"));
        }
        let mut next = current;
        next.revision = expected_revision
            .checked_add(1)
            .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
        next.grants.extend(changes.grants);
        if let Some(categories) = changes.disabled_categories {
            let size = categories.len();
            next.disabled_categories = categories.into_iter().collect();
            if size != next.disabled_categories.len() {
                return Err(RacpError::new("PERMISSIONS_INVALID"));
            }
        }
        if let Some(constraints) = changes.constraints {
            next.constraints = constraints;
        }
        let permissions = next.permissions()?;
        let raw = serde_json::to_vec(&next)?;
        if raw.len() > 65_536 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        atomic_write(&self.root.join(POLICY_FILE), &raw, true)?;
        *self
            .snapshot
            .write()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))? = PolicyCache {
            snapshot: next.clone(),
            permissions,
        };
        Ok(next)
    }
}
/// Future organizational policy can only narrow the local ceiling. No transport is defined.
pub trait ManagedPolicySource: Send + Sync {
    fn allows(&self, request: &serde_json::Value) -> Result<(), RacpError>;
}
