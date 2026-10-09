use super::{
    containment::{self, CommandSpec, OwnedProcess},
    encoding, Provider,
};
use futures_util::future::BoxFuture;
use racp_contract::RacpError;
use racp_core::{error_value, private_dir, AgentSettings, Workspaces};
use serde_json::{json, Value};
use std::{
    io::Write,
    path::PathBuf,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
#[derive(Clone)]
pub struct Shell {
    guards: Workspaces,
    spool: PathBuf,
}
impl Shell {
    pub fn new(settings: &AgentSettings) -> Result<Self, RacpError> {
        let spool = settings.data_dir.join("spool");
        private_dir(&spool)?;
        Ok(Self {
            guards: Workspaces::new(&settings.workspace, &settings.allowed_workspaces)?,
            spool,
        })
    }
    fn run(&self, request: Value, cancel: CancellationToken) -> Result<Value, RacpError> {
        let payload = &request["payload"];
        let started = Instant::now();
        let deadline =
            started + Duration::from_millis(request["remaining_timeout_ms"].as_u64().unwrap_or(1));
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        let argv = containment::argv(payload)?;
        let environment = containment::environment(&payload["env"])?;
        let path = self.guards.path(
            request["context"]["workspace_id"]
                .as_str()
                .unwrap_or("default"),
            payload["cwd"].as_str().unwrap_or("."),
        )?;
        let cwd = self.guards.directory(
            request["context"]["workspace_id"]
                .as_str()
                .unwrap_or("default"),
            &path,
        )?;
        encoding::decode_lossy(b"", payload["encoding"].as_str().unwrap_or("utf-8"))?;
        let id = request["operation_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        if !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let output_path = self.spool.join(format!("{id}.output"));
        let mut output = racp_core::secure_create_file(&output_path)?;
        let spawned = OwnedProcess::spawn(CommandSpec {
            argv,
            environment,
            cwd,
        });
        let mut process = match spawned {
            Ok(p) => p,
            Err(e) => {
                drop(output);
                let _ = std::fs::remove_file(&output_path);
                return Err(e);
            }
        };
        let mut preview = [Vec::new(), Vec::new()];
        let mut totals = [0u64; 2];
        let mut collected = 0u64;
        let mut saved = 0u64;
        let mut position = 0u64;
        let inline = payload["max_output_bytes"].as_u64().unwrap_or(65536) as usize / 2;
        let mut reason = "exited";
        let mut cleanup = "complete";
        let mut ending = None;
        let mut exit_code = None;
        let mut reaped = false;
        let mut buffer = [0u8; 8192];
        const LIMIT: u64 = 64 * 1024 * 1024;
        const SPOOL: u64 = 72 * 1024 * 1024;
        loop {
            for (index, pipe) in [&mut process.stdout, &mut process.stderr]
                .into_iter()
                .enumerate()
            {
                for _ in 0..16 {
                    let n = match pipe.read_available(&mut buffer) {
                        Ok(Some(0)) | Ok(None) => break,
                        Ok(Some(n)) => n,
                        Err(_) => {
                            reason = "pipe_read_failed";
                            break;
                        }
                    };
                    totals[index] += n as u64;
                    if reason != "exited" {
                        continue;
                    }
                    let keep = n.min((LIMIT - collected) as usize);
                    collected += keep as u64;
                    let preview_size = keep.min(inline.saturating_sub(preview[index].len()));
                    preview[index].extend_from_slice(&buffer[..preview_size]);
                    if keep > 0 {
                        let size = keep as u64 + 5;
                        if position + size > SPOOL {
                            reason = "spool_limit";
                        } else {
                            let header =
                                [&[index as u8][..], &(keep as u32).to_be_bytes()[..]].concat();
                            if output
                                .write_all(&header)
                                .and_then(|_| output.write_all(&buffer[..keep]))
                                .is_err()
                            {
                                reason = "spool_write_failed";
                                let _ = output.set_len(position);
                            } else {
                                position += size;
                                saved += keep as u64;
                            }
                        }
                    }
                    if keep < n && reason == "exited" {
                        reason = "output_limit";
                    }
                }
            }
            if !reaped {
                if let Some(code) = process.poll()? {
                    exit_code = Some(code);
                    reaped = true;
                }
            }
            if reason == "exited" && !reaped {
                if cancel.is_cancelled() {
                    reason = "cancelled";
                } else if Instant::now() >= deadline {
                    reason = "timeout";
                }
            }
            if (reaped || reason != "exited") && ending.is_none() {
                if process.kill_tree().is_err() {
                    cleanup = "partial";
                }
                ending = Some(Instant::now());
            }
            if reaped && process.stdout.eof() && process.stderr.eof() && process.tree_empty()? {
                break;
            }
            if ending.is_some_and(|t| t.elapsed() >= Duration::from_secs(5)) {
                cleanup = "partial";
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        if output.sync_all().is_err() {
            reason = "spool_write_failed";
        }
        drop(output);
        drop(process);
        let encoding = payload["encoding"].as_str().unwrap_or("utf-8");
        let decoded = [
            encoding::decode_lossy(&preview[0], encoding)?,
            encoding::decode_lossy(&preview[1], encoding)?,
        ];
        let inline_text = decoded.each_ref().map(|text| {
            let mut n = text.len().min(inline);
            while !text.is_char_boundary(n) {
                n -= 1;
            }
            text[..n].to_owned()
        });
        let truncated = totals[0] > preview[0].len() as u64
            || totals[1] > preview[1].len() as u64
            || inline_text != decoded;
        let mut result = json!({"operation_id":id,"exit_code":if reason=="exited"{exit_code}else{None},"stdout":inline_text[0],"stderr":inline_text[1],"duration_ms":started.elapsed().as_millis()as u64,"truncated":truncated,"artifact_id":null,"termination_reason":reason,"cleanup_status":cleanup,"decoding_errors":decoded.iter().map(|t|t.matches('\u{fffd}').count()).sum::<usize>(),"stdout_bytes":totals[0],"stderr_bytes":totals[1],"artifact_truncated":saved<totals.iter().sum::<u64>(),"collected_bytes":collected,"spooled_bytes":saved,"output_limit_bytes":LIMIT});
        if truncated {
            result["spool_path"] = json!(output_path);
        } else {
            let _ = std::fs::remove_file(output_path);
        }
        let state = match reason {
            "exited" => "SUCCEEDED",
            "timeout" => "TIMED_OUT",
            "cancelled" => "CANCELLED",
            _ => "FAILED",
        };
        let error = if state == "SUCCEEDED" {
            Value::Null
        } else {
            let code = match reason {
                "timeout" => "TIMEOUT",
                "cancelled" => "CANCELLED",
                _ => "RESOURCE_EXHAUSTED",
            };
            let mut error = error_value(code, "contained execution finished", "completed");
            error["details"]["partial_result"] = result.clone();
            error
        };
        Ok(json!({"state":state,"result":result,"error":error}))
    }
}
impl Provider for Shell {
    fn capabilities(&self) -> Vec<Value> {
        vec![
            json!({"name":"shell","version":"1.0.0","operations":["shell.exec"],"installed":true,"supported":true,"enabled":true,"healthy":true,"unavailable_reason":null,"attributes":{"shells":if cfg!(windows){vec!["cmd","powershell"]}else{vec!["bash"]},"containment":if cfg!(windows){"job_object"}else{"process_group"},"output_limit_bytes":64*1024*1024}}),
        ]
    }
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        let this = self.clone();
        Box::pin(async move {
            let outcome = tokio::task::spawn_blocking(move || this.run(request, cancel))
                .await
                .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
            Ok(match outcome {
                Ok(value) => value,
                Err(e) => {
                    json!({"state":match e.code.0{"CANCELLED"=>"CANCELLED","TIMEOUT"=>"TIMED_OUT",_=>"FAILED"},"result":null,"error":error_value(e.code.0,"contained execution unavailable","not_started")})
                }
            })
        })
    }
}
