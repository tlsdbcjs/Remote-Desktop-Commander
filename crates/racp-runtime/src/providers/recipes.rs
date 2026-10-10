//! Fixed workers collect into the private spool; cancellation kills only their Job.
use super::{
    containment::{CommandSpec, OwnedProcess},
    Provider,
};
use futures_util::future::BoxFuture;
use racp_contract::{new_id, timestamp, RacpError};
use racp_core::{AgentSettings, Workspaces};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
#[cfg(windows)]
mod windows;
#[derive(Clone)]
pub struct Recipes {
    spool: PathBuf,
    boot: String,
    device: String,
    active: Arc<AtomicUsize>,
}
impl Recipes {
    pub fn new(s: &AgentSettings, boot: &str) -> Result<Self, RacpError> {
        let spool = s.data_dir.join("spool");
        racp_core::private_dir(&spool)?;
        Ok(Self {
            spool,
            boot: boot.into(),
            device: s.device_id.clone(),
            active: Arc::new(AtomicUsize::new(0)),
        })
    }
    fn collect(&self, r: Value, cancel: CancellationToken) -> Result<Value, RacpError> {
        #[cfg(not(windows))]
        {
            let _ = (r, cancel);
            Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
        }
        #[cfg(windows)]
        {
            let count = self.active.fetch_add(1, Ordering::AcqRel);
            struct Active(Arc<AtomicUsize>);
            impl Drop for Active {
                fn drop(&mut self) {
                    self.0.fetch_sub(1, Ordering::AcqRel);
                }
            }
            let _active = Active(self.active.clone());
            if count >= 4 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let operation = r["operation"].as_str().unwrap_or("");
            let p = racp_contract::validate_operation(operation, r["payload"].clone())?;
            let dump = operation == "process.dump";
            if dump {
                if p["agent_boot_id"] != self.boot {
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
                windows::validate_target(&p)?;
            }
            let deadline = Instant::now()
                + Duration::from_millis(r["remaining_timeout_ms"].as_u64().unwrap_or(1));
            let check = || {
                if cancel.is_cancelled() {
                    Err(RacpError::new("CANCELLED"))
                } else if Instant::now() >= deadline {
                    Err(RacpError::new("TIMEOUT"))
                } else {
                    Ok(())
                }
            };
            check()?;
            let path = self.spool.join(format!(
                "{}.{}.{}",
                r["operation_id"].as_str().unwrap_or("op_invalid"),
                new_id("collection"),
                if dump { "dmp" } else { "pcap" }
            ));
            let guards = Workspaces::new(&self.spool, &[])?;
            let cwd = guards.directory("default", &self.spool)?;
            let exe = std::env::current_exe()?;
            let mut environment = BTreeMap::new();
            for key in ["SystemRoot", "WINDIR", "TEMP", "TMP"] {
                if let Ok(value) = std::env::var(key) {
                    environment.insert(key.into(), value);
                }
            }
            let (mut child, mut input) = OwnedProcess::spawn_stdio(CommandSpec {
                argv: vec![
                    exe.to_string_lossy().into_owned(),
                    "collection-worker".into(),
                ],
                environment,
                cwd,
            })?;
            let _protected = crate::identity::ProtectedProcess::register(child.pid())?;
            let request = json!({"operation":operation,"payload":p,"output":path});
            input.write_all(&serde_json::to_vec(&request)?)?;
            input.write_all(b"\n")?;
            drop(input);
            let mut stdout = vec![];
            let mut stderr = vec![];
            let mut buffer = [0u8; 4096];
            let result = (|| loop {
                check()?;
                for (pipe, output) in [
                    (&mut child.stdout, &mut stdout),
                    (&mut child.stderr, &mut stderr),
                ] {
                    for _ in 0..4 {
                        match pipe.read_available(&mut buffer)? {
                            Some(n) if n > 0 => {
                                if output.len() + n > 8192 {
                                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                                }
                                output.extend_from_slice(&buffer[..n]);
                            }
                            _ => break,
                        }
                    }
                }
                if let Some(code) = child.poll()? {
                    for _ in 0..4 {
                        if let Some(n) = child.stdout.read_available(&mut buffer)? {
                            if stdout.len() + n > 8192 {
                                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                            }
                            stdout.extend_from_slice(&buffer[..n]);
                        }
                    }
                    let receipt: Value = serde_json::from_slice(&stdout)
                        .map_err(|_| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
                    if code != 0 || receipt["status"] != "SUCCEEDED" {
                        return Err(RacpError::new(
                            match receipt["code"].as_str().unwrap_or("") {
                                "PERMISSION_DENIED" => "PERMISSION_DENIED",
                                "PRECONDITION_FAILED" => "PRECONDITION_FAILED",
                                "RESOURCE_EXHAUSTED" => "RESOURCE_EXHAUSTED",
                                "PROCESS_NOT_FOUND" => "PROCESS_NOT_FOUND",
                                _ => "CAPABILITY_UNAVAILABLE",
                            },
                        ));
                    }
                    windows::verify(&path, &receipt, &p, dump, &check)?;
                    return Ok(receipt);
                }
                std::thread::sleep(Duration::from_millis(5));
            })();
            child.kill_tree()?;
            let cleanup = Instant::now() + Duration::from_secs(5);
            while !child.tree_empty()? {
                if Instant::now() >= cleanup {
                    let _ = std::fs::remove_file(&path);
                    return Err(RacpError::new("EXECUTION_UNKNOWN"));
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            let mut receipt = match result {
                Ok(v) => v,
                Err(e) => {
                    let _ = std::fs::remove_file(&path);
                    return Err(e);
                }
            };
            if let Err(e) = check() {
                let _ = std::fs::remove_file(&path);
                return Err(e);
            }
            receipt["device_id"] = json!(self.device);
            receipt["agent_boot_id"] = json!(self.boot);
            receipt["observed_at"] = json!(timestamp());
            receipt["spool_path"] = json!(path);
            receipt["artifact_media_type"] = json!(if dump {
                "application/octet-stream"
            } else {
                "application/vnd.tcpdump.pcap"
            });
            receipt["cleanup_status"] = json!("complete");
            receipt["artifact_id"] = Value::Null;
            Ok(json!({"state":"SUCCEEDED","error":null,"result":receipt}))
        }
    }
}
impl Provider for Recipes {
    fn capabilities(&self) -> Vec<Value> {
        let supported = cfg!(all(windows, target_arch = "x86_64"));
        #[cfg(windows)]
        let admin = windows::administrator();
        #[cfg(not(windows))]
        let admin = false;
        vec![
            json!({"name":"process_dump","version":"1.0.0","operations":["process.dump"],"supported":supported,"enabled":supported,"healthy":supported,"installed":supported,"unavailable_reason":if supported{None}else{Some("requires_windows_x64")},"attributes":{"backend":"Windows DbgHelp","bounded_io":true,"max_bytes":268435456,"modes":["mini","full"]}}),
            json!({"name":"network_capture","version":"1.0.0","operations":["network.capture"],"supported":cfg!(windows),"enabled":admin,"healthy":admin,"installed":cfg!(windows),"unavailable_reason":if admin{None}else{Some("needs_administrator")},"attributes":{"backend":"Windows Winsock SIO_RCVALL","tls_decryption":false,"link_type":"raw_ipv4","max_bytes":16777216}}),
        ]
    }
    fn execute(&self, r: Value, c: CancellationToken) -> BoxFuture<'_, Result<Value, RacpError>> {
        let this = self.clone();
        Box::pin(async move {
            tokio::task::spawn_blocking(move || this.collect(r, c))
                .await
                .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?
        })
    }
}
pub fn run_worker() -> Result<(), RacpError> {
    #[cfg(windows)]
    {
        let mut raw = vec![];
        std::io::stdin().take(16385).read_to_end(&mut raw)?;
        if raw.len() > 16384 {
            return Err(RacpError::new("REQUEST_TOO_LARGE"));
        }
        let value: Value = serde_json::from_slice(&raw)?;
        let operation = value["operation"].as_str().unwrap_or("");
        if !matches!(operation, "network.capture" | "process.dump")
            || value.as_object().is_none_or(|o| o.len() != 3)
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        let p = racp_contract::validate_operation(operation, value["payload"].clone())?;
        let path = Path::new(
            value["output"]
                .as_str()
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?,
        );
        racp_core::validate_local_path(path)?;
        let result = if operation == "process.dump" {
            windows::dump(&p, path)
        } else {
            windows::capture(&p, path)
        };
        match result {
            Ok(receipt) => {
                let mut receipt = receipt;
                receipt["status"] = json!("SUCCEEDED");
                println!("{receipt}");
                Ok(())
            }
            Err(e) => {
                let _ = std::fs::remove_file(path);
                println!("{}", json!({"status":"FAILED","code":e.code}));
                Err(e)
            }
        }
    }
    #[cfg(not(windows))]
    {
        Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
    }
}
