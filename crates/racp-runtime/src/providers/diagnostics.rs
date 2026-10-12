//! Fixed diagnostic fields. No environment, command line, credentials, clipboard or raw event body.
use super::Provider;
use futures_util::future::BoxFuture;
use racp_contract::{new_id, RacpError, VERSION};
use racp_core::AgentSettings;
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
#[cfg(windows)]
mod windows;
#[derive(Clone)]
pub struct Diagnostics {
    spool: PathBuf,
    boot: String,
    device: String,
    policy_revision: Arc<Mutex<u64>>,
}
impl Diagnostics {
    pub fn new(settings: &AgentSettings, boot: &str) -> Self {
        Self {
            spool: settings.data_dir.join("spool"),
            boot: boot.into(),
            device: settings.device_id.clone(),
            policy_revision: Arc::new(Mutex::new(0)),
        }
    }
    fn agent(&self) -> Value {
        json!({"agent_version":VERSION,"device_id":self.device,"agent_boot_id":self.boot,"pid":std::process::id(),"os":std::env::consts::OS,"architecture":std::env::consts::ARCH,"policy_revision":self.policy_revision.lock().map(|v|*v).unwrap_or(0),"uptime_seconds":sysinfo::System::uptime(),"redacted_fields":["credential","token","cookie","clipboard","command_line","environment","gateway","state_paths"]})
    }
    async fn execute_inner(&self, r: Value, cancel: CancellationToken) -> Result<Value, RacpError> {
        let op = r["operation"].as_str().unwrap_or("");
        let p = &r["payload"];
        let deadline = tokio::time::Instant::now()
            + Duration::from_millis(r["remaining_timeout_ms"].as_u64().unwrap_or(30000));
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        match op {
            "diagnostics.agent" => Ok(self.agent()),
            "performance.sample" => {
                let duration = p["duration_ms"].as_u64().unwrap_or(1000);
                let interval = p["interval_ms"].as_u64().unwrap_or(250);
                let end =
                    (tokio::time::Instant::now() + Duration::from_millis(duration)).min(deadline);
                let started = tokio::time::Instant::now();
                let mut samples = vec![];
                loop {
                    let sample=tokio::task::spawn_blocking(||{
                        let mut system=sysinfo::System::new();system.refresh_memory();
                        #[cfg(windows)]let cpu=windows::cpu_times()?;
                        #[cfg(not(windows))]let cpu=Value::Null;
                        Ok::<_,RacpError>(json!({"cpu_times":cpu,"memory_total_bytes":system.total_memory(),"memory_available_bytes":system.available_memory()}))
                    }).await.map_err(|_|RacpError::new("EXECUTION_UNKNOWN"))??;
                    samples.push(
                        json!({"elapsed_ms":started.elapsed().as_millis() as u64,"sample":sample}),
                    );
                    if samples.len() >= 600 || tokio::time::Instant::now() >= end {
                        break;
                    }
                    tokio::select! {_=cancel.cancelled()=>return Err(RacpError::new("CANCELLED")),_=tokio::time::sleep_until(end)=>break,_=tokio::time::sleep(Duration::from_millis(interval))=>{}}
                }
                Ok(
                    json!({"samples":samples,"cpu_measurement":"cumulative_100ns_ticks","sampling_interval_ms":interval,"truncated":samples.len()>=600}),
                )
            }
            "events.query" | "diagnostics.export" => {
                #[cfg(windows)]
                {
                    let provider = self.clone();
                    let request = r.clone();
                    let cancelled = cancel.clone();
                    tokio::task::spawn_blocking(move ||{
                        let filename=format!("{}.json",new_id("diagnostic"));
                        let output=TemporaryOutput(provider.spool.join(&filename));
                        let budget=deadline.saturating_duration_since(tokio::time::Instant::now());
                        let receipt=super::worker::run("diagnostic-worker",&provider.spool,json!({"output_name":filename,"channel":request["payload"]["channel"],"after_record_id":request["payload"]["after_record_id"],"limit":request["payload"]["limit"],"timeout_ms":budget.as_millis().min(120000) as u64}),Instant::now()+budget,&cancelled)?;
                        let mut bytes=vec![];std::fs::File::open(&output.0)?.take(2097153).read_to_end(&mut bytes)?;
                        if bytes.len()>2097152 || receipt["size_bytes"]!=bytes.len() as u64 || receipt["sha256"]!=racp_contract::digest(&bytes){return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));}
                        racp_contract::local_ipc::reject_duplicate_keys(&bytes)?;
                        let events:Value=serde_json::from_slice(&bytes)?;
                        if cancelled.is_cancelled(){return Err(RacpError::new("CANCELLED"));}
                        if tokio::time::Instant::now()>=deadline{return Err(RacpError::new("TIMEOUT"));}
                        if request["operation"]=="events.query"{return Ok(events);}
                        let report=json!({"agent":provider.agent(),"events":events,"redaction":"event system metadata only; sensitive event data omitted"});
                        let bytes=serde_json::to_vec(&report)?;if bytes.len() as u64>request["payload"]["max_bytes"].as_u64().unwrap_or(1048576){return Err(RacpError::new("RESOURCE_EXHAUSTED"));}
                        let path=provider.spool.join(format!("{}.diagnostics.json",request["operation_id"].as_str().unwrap_or("op_invalid")));
                        racp_core::atomic_write(&path,&bytes,false)?;
                        if cancelled.is_cancelled(){let _=std::fs::remove_file(path);return Err(RacpError::new("CANCELLED"));}
                        Ok(json!({"spool_path":path,"artifact_media_type":"application/json","sha256":racp_contract::digest(&bytes),"size_bytes":bytes.len(),"redacted":true,"report_id":new_id("diagnostics")}))
                    }).await.map_err(|_|RacpError::new("EXECUTION_UNKNOWN"))?
                }
                #[cfg(not(windows))]
                {
                    Err(RacpError::new("OPERATION_NOT_SUPPORTED"))
                }
            }
            _ => Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
        }
    }
}
struct TemporaryOutput(PathBuf);
impl Drop for TemporaryOutput {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkerInput {
    output_name: String,
    channel: String,
    after_record_id: u64,
    limit: u64,
    timeout_ms: u64,
}
/// A fixed Rust child owns potentially blocking Event Log calls. The parent owns its Job and budget.
pub fn run_diagnostic_worker() -> Result<(), RacpError> {
    let mut bytes = vec![];
    std::io::stdin().take(16385).read_to_end(&mut bytes)?;
    if bytes.len() > 16384 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    racp_contract::local_ipc::reject_duplicate_keys(&bytes)?;
    let input: WorkerInput = serde_json::from_slice(&bytes)?;
    let uuid = input
        .output_name
        .strip_prefix("diagnostic_")
        .and_then(|s| s.strip_suffix(".json"))
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
    if uuid.len() != 32
        || !uuid.bytes().all(|b| b.is_ascii_hexdigit())
        || !["System", "Application", "Security"].contains(&input.channel.as_str())
        || input.after_record_id > i64::MAX as u64
        || input.limit == 0
        || input.limit > 1000
        || input.timeout_ms == 0
        || input.timeout_ms > 120000
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    #[cfg(windows)]
    let events = windows::events(
        &json!({"channel":input.channel,"after_record_id":input.after_record_id,"limit":input.limit}),
        Instant::now() + Duration::from_millis(input.timeout_ms),
        &CancellationToken::new(),
    )?;
    #[cfg(not(windows))]
    return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
    #[cfg(windows)]
    {
        let bytes = serde_json::to_vec(&events)?;
        if bytes.len() > 2097152 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let path = std::env::current_dir()?.join(input.output_name);
        racp_core::atomic_write(&path, &bytes, false)?;
        serde_json::to_writer(
            std::io::stdout(),
            &json!({"ok":true,"result":{"sha256":racp_contract::digest(&bytes),"size_bytes":bytes.len()}}),
        )?;
        std::io::stdout().flush()?;
        Ok(())
    }
}
impl Provider for Diagnostics {
    fn policy_changed(
        &self,
        policy: racp_core::policy::PolicySnapshot,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            *self
                .policy_revision
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))? = policy.revision;
            Ok(())
        })
    }
    fn capabilities(&self) -> Vec<Value> {
        vec![
            json!({"name":"diagnostics","version":"1.0.0","operations":["diagnostics.agent","diagnostics.export"],"supported":true,"healthy":true,"enabled":true}),
            json!({"name":"performance","version":"1.0.0","operations":["performance.sample"],"supported":true,"healthy":true,"enabled":true}),
            json!({"name":"events","version":"1.0.0","operations":["events.query"],"supported":cfg!(windows),"healthy":cfg!(windows),"enabled":cfg!(windows)}),
        ]
    }
    fn execute(
        &self,
        r: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        Box::pin(async move {
            let result = self.execute_inner(r, cancel).await?;
            Ok(json!({"state":"SUCCEEDED","result":result,"error":null}))
        })
    }
}
