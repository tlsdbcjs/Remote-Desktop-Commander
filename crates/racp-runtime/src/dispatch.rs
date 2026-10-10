use crate::Agent;
use futures_util::FutureExt;
use racp_contract::{registry, validate_operation, RacpError};
use racp_core::{error_value, Acceptance, Journal, Record};
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;

#[derive(Clone)]
pub(crate) struct Execution {
    cancel: CancellationToken,
    reason: Arc<Mutex<String>>,
}
fn allowed(operation: &str, profile: &str) -> bool {
    profile == "trusted_personal"
        || profile == "standard"
        || (!registry()[operation]["side_effect"]
            .as_bool()
            .unwrap_or(true)
            && !["process.memory_read", "process.memory_regions"].contains(&operation))
}
impl Agent {
    pub(crate) fn authorize_record(&self, record: &Record) -> Result<(), RacpError> {
        let permissions = self
            .settings
            .permissions
            .clone()
            .unwrap_or_else(|| racp_core::legacy_permissions(self.settings.desktop_enabled));
        racp_core::authorize(
            &permissions,
            &record.request,
            self.providers.owns_process(&record.request),
        )?;
        let operation = record.request["operation"].as_str().unwrap_or("");
        if !allowed(operation, &self.settings.profile) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let outputs = self.outputs.descriptors(&record.id)?;
        let ceiling = permissions["constraints"]["max_output_bytes"]
            .as_u64()
            .unwrap_or(1073741824);
        let mut total = 0u64;
        for output in &outputs {
            total = total
                .checked_add(
                    output["size_bytes"]
                        .as_u64()
                        .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
                )
                .filter(|n| *n <= ceiling)
                .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        }
        if !outputs.is_empty()
            && (!racp_core::permission_allows_field(&permissions, "artifacts.export")
                || matches!(
                    operation,
                    "process.inspect" | "process.list" | "process.tree"
                ) && !racp_core::permission_allows_field(&permissions, "process.arguments.read"))
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if serde_json::to_vec(&record.result)?.len() as u64 > ceiling {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(())
    }
    fn redact_arguments(&self, value: &mut Value) {
        let permissions = self
            .settings
            .permissions
            .clone()
            .unwrap_or_else(|| racp_core::legacy_permissions(self.settings.desktop_enabled));
        if racp_core::permission_allows_field(&permissions, "process.arguments.read") {
            return;
        }
        fn visit(value: &mut Value) {
            match value {
                Value::Object(map) => {
                    if map.contains_key("cmdline") {
                        map.insert("cmdline".into(), json!([]));
                        map.insert("arguments_included".into(), json!(false));
                    }
                    for child in map.values_mut() {
                        visit(child);
                    }
                }
                Value::Array(items) => {
                    for child in items {
                        visit(child);
                    }
                }
                _ => (),
            }
        }
        visit(value);
    }
    pub(crate) async fn outcome(&self, record: Record) -> Result<Value, RacpError> {
        let permissions = self
            .settings
            .permissions
            .clone()
            .unwrap_or_else(|| racp_core::legacy_permissions(self.settings.desktop_enabled));
        let operation = record.request["operation"].as_str().unwrap_or("");
        let authorization = self.authorize_record(&record);
        if authorization.is_err() || !allowed(operation, &self.settings.profile) {
            let mut value = self.base("error").await;
            for key in ["request_id", "trace_id"] {
                value[key] = record.request[key].clone();
            }
            value["operation_id"] = json!(record.id);
            value["code"] = json!(authorization
                .err()
                .map(|e| e.code.0)
                .unwrap_or("PERMISSION_DENIED"));
            return Ok(value);
        }
        let mut value = self
            .base(if record.outcome_available {
                "result"
            } else {
                "error"
            })
            .await;
        for key in ["request_id", "trace_id"] {
            value[key] = record.request[key].clone();
        }
        value["operation_id"] = json!(record.id);
        if record.outcome_available {
            value["state"] = json!(record.state);
            value["result"] = json!(record.result);
            if matches!(
                operation,
                "process.list" | "process.inspect" | "process.tree"
            ) {
                self.redact_arguments(&mut value["result"]);
            }
            value["error"] = json!(record.error);
            value["outputs"] = json!(self.outputs.descriptors(&record.id)?);
        } else {
            value["code"] = json!("OPERATION_EXPIRED");
        }
        Ok(value)
    }
    pub(crate) async fn dispatch(&self, mut request: Value) -> Result<(), RacpError> {
        let deadline = tokio::time::Instant::now()
            + Duration::from_millis(
                request["remaining_timeout_ms"]
                    .as_u64()
                    .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?,
            );
        let op = request["operation"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
            .to_string();
        request["payload"] = validate_operation(&op, request["payload"].clone())?;
        let id = request["operation_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
            .to_string();
        let acceptance = match self.journal.accept(&request) {
            Ok(a) => a,
            Err(e) if e.code.0 == "OPERATION_EXPIRED" => {
                let record = self.journal.record_for_key(&request)?;
                // accept checked the key/hash; correlation must still identify this operation.
                if record.id != id
                    || record.request["request_id"] != request["request_id"]
                    || record.request["trace_id"] != request["trace_id"]
                {
                    return Err(RacpError::new("IDEMPOTENCY_CONFLICT"));
                }
                return self.send(self.outcome(record).await?).await;
            }
            Err(e) => return Err(e),
        };
        if acceptance.record().id != id
            || acceptance.record().request["request_id"] != request["request_id"]
            || acceptance.record().request["trace_id"] != request["trace_id"]
        {
            return Err(RacpError::new("IDEMPOTENCY_CONFLICT"));
        }
        let mut ack = self.base("ack").await;
        for key in ["request_id", "operation_id", "trace_id"] {
            ack[key] = request[key].clone();
        }
        self.send(ack).await?;
        if let Acceptance::Replay(record) = acceptance {
            return self.send(self.outcome(record).await?).await;
        }
        // An accepted operation whose ACK was lost is safe to start once on re-dispatch.
        let mut tasks = self.tasks.lock().await;
        if tasks.contains_key(&id) {
            return Ok(());
        }
        if tasks.len() >= 16 {
            drop(tasks);
            let record = self.journal.transition(
                &id,
                "FAILED",
                None,
                Some(error_value(
                    "RESOURCE_EXHAUSTED",
                    "agent concurrency limit reached",
                    "not_started",
                )),
            )?;
            return self.send(self.outcome(record).await?).await;
        }
        if self.journal.get(&id)?.state != "ACCEPTED" {
            return Ok(());
        }
        let execution = Execution {
            cancel: CancellationToken::new(),
            reason: Arc::new(Mutex::new("requested".into())),
        };
        tasks.insert(id.clone(), execution.clone());
        drop(tasks);
        let agent = self.clone();
        tokio::spawn(async move {
            let work = std::panic::AssertUnwindSafe(agent.execute(request, execution, deadline))
                .catch_unwind()
                .await;
            if !matches!(work, Ok(Ok(()))) {
                let _ = agent.journal.transition(
                    &id,
                    "UNKNOWN",
                    None,
                    Some(error_value(
                        "EXECUTION_UNKNOWN",
                        "execution ended without a durable result",
                        "unknown",
                    )),
                );
            }
            let input = agent.outputs.root().join(format!("{id}.input"));
            if racp_core::validate_local_path(&input).is_ok() {
                let _ = std::fs::remove_file(input);
            }
            let _ = agent.outputs.release(&id);
            agent.tasks.lock().await.remove(&id);
            agent.completed.notify_waiters();
            if let Ok(record) = agent.journal.get(&id) {
                agent.log("agent_result",json!({"operation":record.request["operation"],"operation_id":id,"state":record.state}));
                if let Ok(message) = agent.outcome(record).await {
                    let _ = agent.send(message).await;
                }
            }
        });
        Ok(())
    }
    async fn execute(
        &self,
        mut request: Value,
        execution: Execution,
        deadline: tokio::time::Instant,
    ) -> Result<(), RacpError> {
        let id = request["operation_id"].as_str().unwrap().to_string();
        let op = request["operation"].as_str().unwrap().to_string();
        let work=async{
            let permissions=self.settings.permissions.clone().unwrap_or_else(||racp_core::legacy_permissions(self.settings.desktop_enabled));
            racp_core::authorize(&permissions,&request,self.providers.owns_process(&request))?;
            self.outputs.reserve(&id,&op,&request["payload"])?;
            let profile=request["context"]["execution_profile_id"].as_str().unwrap_or("read_only");
            if !allowed(&op,&self.settings.profile) || !allowed(&op,profile) || (profile=="trusted_personal"&&self.settings.profile!="trusted_personal"&&registry()[&op]["side_effect"]==true){return Err(RacpError::new("PERMISSION_DENIED"));}
            let workspace=request["context"]["workspace_id"].as_str().unwrap_or("default");
            if workspace!="default" && !self.settings.allowed_workspaces.iter().any(|s|s.id==workspace){return Err(RacpError::new("WORKSPACE_NOT_FOUND"));}
            if tokio::time::Instant::now()>=*self.lease.lock().await {return Err(RacpError::new("DEVICE_OFFLINE"));}
            if deadline<=tokio::time::Instant::now(){return Err(RacpError::new("TIMEOUT"));}
            if execution.cancel.is_cancelled(){return Err(RacpError::new("CANCELLED"));}
            self.journal.transition(&id,"RUNNING",None,None)?;
            self.log("agent_execution_started",json!({"operation":op,"operation_id":id,"state":"RUNNING"}));
            if request["execution_mode"]=="job"{self.job_progress(&request,if op=="process.wait"{"WAITING"}else{"RUNNING"},if op=="process.wait"{Some("process_exit")}else{None}).await?;}
            if ["filesystem.write","browser.upload"].contains(&op.as_str()) {
                if let Some(artifact)=request["payload"]["artifact_id"].as_str(){
                    let path=self.outputs.root().join(format!("{id}.input"));
                    let metadata=tokio::select! {
                        _=execution.cancel.cancelled()=>return Err(RacpError::new("CANCELLED")),
                        _=tokio::time::sleep_until(deadline)=>return Err(RacpError::new("TIMEOUT")),
                        downloaded=self.artifacts.download(artifact,&path,&id)=>downloaded?,
                    };
                    request["payload"]["_artifact_path"]=json!(path);request["payload"]["_artifact_sha256"]=metadata["sha256"].clone();request["payload"]["_artifact_size"]=metadata["size_bytes"].clone();
                }
            }
            request["remaining_timeout_ms"]=json!((deadline.saturating_duration_since(tokio::time::Instant::now()).as_millis() as u64).max(1));
            let provider=self.providers.execute(request,execution.cancel.clone());tokio::pin!(provider);
            tokio::select!{biased;
                result=&mut provider=>result,
                _=tokio::time::sleep_until(deadline)=>{
                    *execution.reason.lock().await="deadline".into();execution.cancel.cancel();provider.await
                }
            }
        }.await;
        let result = match work {
            Ok(mut outcome) => {
                if !Journal::terminal(outcome["state"].as_str().unwrap_or(""))
                    || (!outcome["result"].is_null() && !outcome["result"].is_object())
                    || (!outcome["error"].is_null() && !outcome["error"].is_object())
                {
                    return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
                }
                if outcome["state"] == "CANCELLED" && *execution.reason.lock().await == "deadline" {
                    outcome["state"] = json!("TIMED_OUT");
                    outcome["error"] =
                        error_value("TIMEOUT", "execution budget elapsed", "unknown");
                }
                if matches!(
                    op.as_str(),
                    "process.list" | "process.inspect" | "process.tree"
                ) {
                    self.redact_arguments(&mut outcome["result"]);
                }
                self.promote_output(&id, &mut outcome).await?;
                self.journal.transition(
                    &id,
                    outcome["state"].as_str().unwrap(),
                    if outcome["result"].is_null() {
                        None
                    } else {
                        Some(outcome["result"].clone())
                    },
                    if outcome["error"].is_null() {
                        None
                    } else {
                        Some(outcome["error"].clone())
                    },
                )
            }
            Err(mut error) => {
                if error.code.0 == "CANCELLED" && *execution.reason.lock().await == "deadline" {
                    error = RacpError::new("TIMEOUT");
                }
                let state = match error.code.0 {
                    "TIMEOUT" => "TIMED_OUT",
                    "CANCELLED" => "CANCELLED",
                    "EXECUTION_UNKNOWN" => "UNKNOWN",
                    _ => "FAILED",
                };
                self.journal.transition(
                    &id,
                    state,
                    None,
                    Some(error_value(
                        error.code.0,
                        "execution interrupted or rejected",
                        if state == "UNKNOWN" {
                            "unknown"
                        } else {
                            "not_started"
                        },
                    )),
                )
            }
        };
        self.outputs.release(&id)?;
        result?;
        Ok(())
    }
    async fn promote_output(&self, id: &str, outcome: &mut Value) -> Result<(), RacpError> {
        let permissions = self
            .settings
            .permissions
            .clone()
            .unwrap_or_else(|| racp_core::legacy_permissions(self.settings.desktop_enabled));
        let limit = permissions["constraints"]["max_output_bytes"]
            .as_u64()
            .unwrap_or(1073741824);
        let mut total = 0u64;
        let mut exporting = false;
        for pointer in [
            "/result/spool_path",
            "/result/preview/spool_path",
            "/error/details/report_spool_path",
        ] {
            if let Some(path) = outcome.pointer(pointer).and_then(Value::as_str) {
                let path = std::path::Path::new(path);
                if !racp_core::path_within(path, self.outputs.root()) {
                    return Err(RacpError::new("PATH_ACCESS_DENIED"));
                }
                let file = racp_core::secure_read_file(path)?;
                total = total
                    .checked_add(file.metadata()?.len())
                    .filter(|n| *n <= limit)
                    .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
                exporting = true;
            }
        }
        let inline = serde_json::to_vec(&outcome["result"])?;
        if inline.len() as u64 > limit {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        if (exporting || inline.len() > 65536)
            && !racp_core::permission_allows_field(&permissions, "artifacts.export")
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if let Some(report) = outcome["error"]["details"]["report_spool_path"]
            .as_str()
            .map(std::path::PathBuf::from)
        {
            let outputs = self.outputs.clone();
            let id = id.to_string();
            let output = tokio::task::spawn_blocking(move || {
                outputs.register(&report, &id, "application/json")
            })
            .await
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))??;
            outcome["error"]["details"]
                .as_object_mut()
                .unwrap()
                .remove("report_spool_path");
            outcome["error"]["details"]["report_output_id"] = output["id"].clone();
            outcome["error"]["details"]["report_upload_status"] = json!("pending");
        }
        let result = &mut outcome["result"];
        if result.is_null() {
            return Ok(());
        }
        if !result["spool_path"].is_string() && serde_json::to_vec(result)?.len() > 65536 {
            let path = self.outputs.root().join(format!("{id}.json"));
            racp_core::atomic_write(&path, &serde_json::to_vec(result)?, true)?;
            *result = json!({"artifact_id":null,"spool_path":path,"artifact_media_type":"application/json","truncated":true});
        }
        // A desktop capture can have an independently uploaded PNG preview.
        for pointer in ["", "/preview"] {
            let Some(result) = result.pointer_mut(pointer) else {
                continue;
            };
            if let Some(path) = result["spool_path"].as_str().map(std::path::PathBuf::from) {
                let outputs = self.outputs.clone();
                let id = id.to_string();
                let media = result["artifact_media_type"]
                    .as_str()
                    .unwrap_or("application/vnd.racp.output-stream")
                    .to_string();
                let output =
                    tokio::task::spawn_blocking(move || outputs.register(&path, &id, &media))
                        .await
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))??;
                result.as_object_mut().unwrap().remove("spool_path");
                result["output_id"] = output["id"].clone();
                result["artifact_upload_status"] = json!("pending");
            }
        }
        if outcome["error"]["details"]["partial_result"].is_object() {
            outcome["error"]["details"]["partial_result"] = outcome["result"].clone();
        }
        Ok(())
    }
    pub(crate) async fn cancel_execution(&self, id: &str, reason: &str) -> Result<(), RacpError> {
        let task = self.tasks.lock().await.get(id).cloned();
        if let Some(task) = task {
            *task.reason.lock().await = reason.into();
            self.journal
                .transition(id, "CANCEL_REQUESTED", None, None)?;
            task.cancel.cancel();
        }
        Ok(())
    }
    pub(crate) async fn cancel_all(&self, reason: &str) -> Result<(), RacpError> {
        let ids: Vec<String> = self.tasks.lock().await.keys().cloned().collect();
        for id in ids {
            self.cancel_execution(&id, reason).await?;
        }
        Ok(())
    }
    pub(crate) async fn job_progress(
        &self,
        request: &Value,
        state: &str,
        waiting: Option<&str>,
    ) -> Result<(), RacpError> {
        let revision = self.journal.progress(request, state, waiting)?;
        let mut update = self.base("event").await;
        for key in ["request_id", "operation_id", "trace_id"] {
            update[key] = request[key].clone();
        }
        update["name"] = json!("job.state_changed");
        update["state"] = json!(state);
        update["waiting_reason"] = json!(waiting);
        update["progress"] = Value::Null;
        update["revision"] = json!(revision);
        let _ = self.send(update).await;
        Ok(())
    }
    pub(crate) async fn execution_inventory(&self) -> Result<Vec<Value>, RacpError> {
        let mut items = vec![];
        for id in self.tasks.lock().await.keys() {
            let record = self.journal.get(id)?;
            if Journal::terminal(&record.state) {
                continue;
            }
            let progress = self.journal.get_progress(id)?;
            items.push(json!({"operation_id":id,"request_id":record.request["request_id"],"trace_id":record.request["trace_id"],"state":record.state,"job_state":progress["state"],"waiting_reason":progress["waiting_reason"],"progress":progress["progress"],"progress_revision":progress["revision"].as_u64().unwrap_or(0)}));
        }
        Ok(items)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::{peer::Peer, providers::Provider};
    use futures_util::future::BoxFuture;
    use racp_core::AgentSettings;
    use serde_json::json;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };
    use std::time::Duration;
    use tokio_util::sync::CancellationToken;
    struct Controlled {
        effects: Arc<AtomicUsize>,
        slow: bool,
    }
    impl Provider for Controlled {
        fn capabilities(&self) -> Vec<Value> {
            vec![
                json!({"name":"shell","version":"1.0.0","operations":["shell.exec"],"healthy":true,"enabled":true}),
            ]
        }
        fn execute(
            &self,
            _: Value,
            cancel: CancellationToken,
        ) -> BoxFuture<'_, Result<Value, RacpError>> {
            Box::pin(async move {
                self.effects.fetch_add(1, Ordering::SeqCst);
                if self.slow {
                    cancel.cancelled().await;
                    return Err(RacpError::new("CANCELLED"));
                }
                Ok(json!({"state":"SUCCEEDED","result":{"exit_code":0},"error":null}))
            })
        }
    }
    fn request() -> Value {
        json!({"protocol":1,"type":"request","device_id":"dev_1","agent_boot_id":"boot_1","connection_epoch":1,"request_id":"req_1","operation_id":"op_1","trace_id":"0".repeat(32),"timestamp":"2026-10-10T00:00:00Z","operation":"shell.exec","timeout_ms":60000,"remaining_timeout_ms":60000,"execution_mode":"sync","idempotency_key":"key","context":{"principal_id":"owner_local","workspace_id":"default","execution_profile_id":"trusted_personal","policy_revision":1},"payload":{"argv":["echo","test"]},"retain_key":true})
    }
    async fn agent(root: &std::path::Path, profile: &str, slow: bool) -> (Agent, Arc<AtomicUsize>) {
        let workspace = root.join("workspace");
        std::fs::create_dir(&workspace).unwrap();
        let settings:AgentSettings=serde_json::from_value(json!({"version":1,"gateway":"http://localhost:1234","device_id":"dev_1","workspace":workspace,"data_dir":root.join("data"),"profile":profile,"allowed_workspaces":[],"ca_file":null,"desktop_enabled":false})).unwrap();
        let mut agent = Agent::new(settings, "a".repeat(32)).unwrap();
        let effects = Arc::new(AtomicUsize::new(0));
        agent.providers = Arc::new(Controlled {
            effects: effects.clone(),
            slow,
        });
        *agent.peer.write().await = Some(Peer::spawn(
            futures_util::sink::drain(),
            CancellationToken::new(),
        ));
        *agent.lease.lock().await = tokio::time::Instant::now() + Duration::from_secs(60);
        agent.status.write().await["connection_epoch"] = json!(1);
        (agent, effects)
    }
    async fn settled(agent: &Agent) -> Value {
        for _ in 0..300 {
            let record = agent.journal.get("op_1").unwrap();
            if racp_core::Journal::terminal(&record.state) {
                return serde_json::to_value(record).unwrap();
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("execution did not settle");
    }
    #[tokio::test]
    async fn duplicate_never_executes_twice_and_different_operation_is_fenced() {
        let root = tempfile::tempdir().unwrap();
        let (agent, effects) = agent(root.path(), "trusted_personal", false).await;
        for _ in 0..100 {
            agent.dispatch(request()).await.unwrap();
        }
        assert_eq!(settled(&agent).await["state"], "SUCCEEDED");
        assert_eq!(effects.load(Ordering::SeqCst), 1);
        let mut other = request();
        other["operation_id"] = json!("op_other");
        assert_eq!(
            agent.dispatch(other).await.unwrap_err().code.0,
            "IDEMPOTENCY_CONFLICT"
        );
    }
    #[tokio::test]
    async fn local_policy_and_expired_lease_deny_before_effect() {
        for profile in ["read_only", "trusted_personal"] {
            let root = tempfile::tempdir().unwrap();
            let (agent, effects) = agent(root.path(), profile, false).await;
            if profile == "trusted_personal" {
                *agent.lease.lock().await = tokio::time::Instant::now();
            }
            agent.dispatch(request()).await.unwrap();
            let record = settled(&agent).await;
            assert_eq!(record["state"], "FAILED");
            assert_eq!(
                record["error"]["code"],
                if profile == "read_only" {
                    "PERMISSION_DENIED"
                } else {
                    "DEVICE_OFFLINE"
                }
            );
            assert_eq!(effects.load(Ordering::SeqCst), 0);
        }
    }
    #[tokio::test]
    async fn deadline_cancellation_waits_for_provider_cleanup() {
        let root = tempfile::tempdir().unwrap();
        let (agent, effects) = agent(root.path(), "trusted_personal", true).await;
        let mut r = request();
        r["remaining_timeout_ms"] = json!(1000);
        agent.dispatch(r).await.unwrap();
        assert_eq!(settled(&agent).await["state"], "TIMED_OUT");
        assert_eq!(effects.load(Ordering::SeqCst), 1);
    }
}
