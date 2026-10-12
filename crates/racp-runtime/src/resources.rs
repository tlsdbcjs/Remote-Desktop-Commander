//! Scoped retirement closes output fences and retains timed-out cleanup for diagnosis.
use futures_util::future::BoxFuture;
use racp_contract::RacpError;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::sync::Mutex;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResourceScope {
    pub device_id: String,
    pub boot_id: String,
    pub epoch: u64,
    pub workspace_id: String,
    pub policy_revision: u64,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RetirementScope {
    pub device_id: Option<String>,
    pub boot_id: Option<String>,
    pub epoch: Option<u64>,
    pub workspace_id: Option<String>,
    pub before_policy_revision: Option<u64>,
    pub resource_ids: Option<Vec<String>>,
}
impl RetirementScope {
    fn matches(&self, id: &str, scope: &ResourceScope) -> bool {
        self.device_id
            .as_ref()
            .is_none_or(|v| v == &scope.device_id)
            && self.boot_id.as_ref().is_none_or(|v| v == &scope.boot_id)
            && self.epoch.is_none_or(|v| v == scope.epoch)
            && self
                .workspace_id
                .as_ref()
                .is_none_or(|v| v == &scope.workspace_id)
            && self
                .before_policy_revision
                .is_none_or(|v| scope.policy_revision < v)
            && self
                .resource_ids
                .as_ref()
                .is_none_or(|v| v.iter().any(|v| v == id))
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceKind {
    InputLease,
    Execution,
    Output,
    OwnedHelper,
    Session,
    Artifact,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Ownership {
    Owned,
    Borrowed,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RetirementReport {
    pub retired_resource_ids: Vec<String>,
    pub failed_resource_ids: Vec<String>,
    pub quarantined_resource_ids: Vec<String>,
    pub borrowed_target_ids_preserved: Vec<String>,
}
pub type Retirer = Arc<dyn Fn() -> BoxFuture<'static, Result<(), RacpError>> + Send + Sync>;
pub struct Resource {
    pub id: String,
    pub scope: ResourceScope,
    pub kind: ResourceKind,
    pub ownership: Ownership,
    pub terminates_target: bool,
    pub request: serde_json::Value,
    pub retire: Retirer,
}
struct Entry {
    resource: Resource,
    active: AtomicBool,
    cleanup: Mutex<Option<tokio::task::JoinHandle<Result<(), RacpError>>>>,
}
#[derive(Clone)]
pub struct ResourceRegistry {
    entries: Arc<Mutex<BTreeMap<String, Arc<Entry>>>>,
    retirement: Arc<Mutex<()>>,
    workers: Arc<tokio::sync::Semaphore>,
}
impl Default for ResourceRegistry {
    fn default() -> Self {
        Self {
            entries: Default::default(),
            retirement: Default::default(),
            workers: Arc::new(tokio::sync::Semaphore::new(16)),
        }
    }
}
impl ResourceRegistry {
    pub async fn register(&self, resource: Resource) -> Result<(), RacpError> {
        if resource.id.is_empty()
            || resource.id.len() > 256
            || (resource.ownership == Ownership::Borrowed && resource.terminates_target)
        {
            return Err(RacpError::new("RESOURCE_SCOPE_INVALID"));
        }
        let mut entries = self.entries.lock().await;
        if entries.len() >= 4096 || entries.contains_key(&resource.id) {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        entries.insert(
            resource.id.clone(),
            Arc::new(Entry {
                resource,
                active: AtomicBool::new(true),
                cleanup: Mutex::new(None),
            }),
        );
        Ok(())
    }
    pub async fn remove(&self, id: &str) {
        self.entries.lock().await.remove(id);
    }
    pub async fn output_allowed(&self, id: &str) -> bool {
        self.entries
            .lock()
            .await
            .get(id)
            .is_some_and(|e| e.active.load(Ordering::Acquire))
    }
    pub async fn requests(&self) -> Vec<(String, serde_json::Value)> {
        self.entries
            .lock()
            .await
            .iter()
            .filter(|(_, e)| e.active.load(Ordering::Acquire))
            .map(|(id, e)| (id.clone(), e.resource.request.clone()))
            .collect()
    }
    pub async fn inventory(&self) -> Vec<serde_json::Value> {
        self.entries.lock().await.values().map(|e| serde_json::json!({"id":e.resource.id,"scope":e.resource.scope,"kind":e.resource.kind,"ownership":e.resource.ownership,"active":e.active.load(Ordering::Acquire)})).collect()
    }
    pub async fn retire(&self, scope: RetirementScope, budget: Duration) -> RetirementReport {
        let _serial = self.retirement.lock().await;
        let mut entries: Vec<_> = self
            .entries
            .lock()
            .await
            .values()
            .filter(|e| scope.matches(&e.resource.id, &e.resource.scope))
            .cloned()
            .collect();
        entries.sort_by_key(|e| (e.resource.kind, e.resource.id.clone()));
        for entry in &entries {
            entry.active.store(false, Ordering::Release);
        }
        let deadline = tokio::time::Instant::now() + budget;
        let mut report = RetirementReport::default();
        for entry in entries {
            let id = entry.resource.id.clone();
            if entry.resource.ownership == Ownership::Borrowed {
                report.borrowed_target_ids_preserved.push(id.clone());
            }
            let mut cleanup = entry.cleanup.lock().await;
            if cleanup.is_none() {
                let permit =
                    match tokio::time::timeout_at(deadline, self.workers.clone().acquire_owned())
                        .await
                    {
                        Ok(Ok(permit)) => permit,
                        _ => {
                            report.failed_resource_ids.push(id.clone());
                            report.quarantined_resource_ids.push(id);
                            continue;
                        }
                    };
                let retire = entry.resource.retire.clone();
                *cleanup = Some(tokio::spawn(async move {
                    let _permit = permit;
                    retire().await
                }));
            }
            match tokio::time::timeout_at(deadline, cleanup.as_mut().unwrap()).await {
                Ok(Ok(Ok(()))) => {
                    *cleanup = None;
                    drop(cleanup);
                    self.remove(&id).await;
                    report.retired_resource_ids.push(id);
                }
                Ok(_) => {
                    *cleanup = None;
                    report.failed_resource_ids.push(id.clone());
                    report.quarantined_resource_ids.push(id);
                }
                Err(_) => {
                    report.failed_resource_ids.push(id.clone());
                    report.quarantined_resource_ids.push(id);
                }
            }
        }
        report
    }
}
