use super::{
    native::NativeDesktop, native_identity::PinnedPeer, pipe::PipeServer, DesktopState, PairConfig,
    Scope,
};
use base64::Engine;
use racp_contract::{digest, new_id, timestamp, validate_operation, RacpError};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::Path,
    time::{Duration, Instant},
};
struct Capture {
    bytes: Vec<u8>,
    scope: Scope,
    operation: String,
    expires: Instant,
}
struct Broker {
    native: NativeDesktop,
    automation: Option<super::automation::Automation>,
    authority: DesktopState,
    device: String,
    captures: BTreeMap<String, Capture>,
    stopped: bool,
    pair_path: std::path::PathBuf,
}
pub fn run_broker(config_path: &Path) -> Result<(), RacpError> {
    racp_core::validate_local_path(config_path)?;
    let values = racp_core::SecretStore::new(config_path.into()).load()?;
    let config = PairConfig::decode(
        values
            .get("pair")
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?
            .as_bytes(),
    )?;
    let device = values
        .get("device_id")
        .filter(|v| v.len() <= 96 && !v.is_empty())
        .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?
        .clone();
    let controller = PinnedPeer::open(config.agent_pid)?;
    config.require_agent(controller.identity())?;
    let guardian_pid = values
        .get("guardian_pid")
        .and_then(|s| s.parse::<u32>().ok())
        .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
    let guardian = PinnedPeer::open(guardian_pid)?;
    config.require_broker(guardian.identity())?;
    if values
        .get("guardian_created")
        .and_then(|s| s.parse::<f64>().ok())
        != Some(guardian.identity().created)
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut broker = Broker {
        native: NativeDesktop::new(
            config.session_id,
            super::hooks::marker(&config.secret),
            guardian,
            config_path.with_file_name("guardian-status.json"),
        )?,
        automation: super::automation::Automation::new().ok(),
        authority: DesktopState::new(config.session_id),
        device,
        captures: BTreeMap::new(),
        stopped: false,
        pair_path: config_path.into(),
    };
    let mut server = PipeServer::new(config)?;
    while !broker.stopped {
        controller.alive()?;
        broker.captures.retain(|_, c| c.expires > Instant::now());
        // Invalid/expired exchanges disconnect only that client; the next authenticated client can retry.
        let _ = server.accept(|request| broker.execute(request));
    }
    Ok(())
}
impl Broker {
    fn capture_store(
        &mut self,
        bytes: Vec<u8>,
        scope: &Scope,
        operation: &str,
    ) -> Result<Value, RacpError> {
        let size = bytes.len();
        if self.captures.len() >= 8
            || self.captures.values().map(|c| c.bytes.len()).sum::<usize>() + size
                > 64 * 1024 * 1024
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let id = new_id("capture");
        let value = json!({"capture_id":id,"size_bytes":size,"sha256":digest(&bytes),"media_type":"image/png"});
        self.captures.insert(
            id,
            Capture {
                bytes,
                scope: scope.clone(),
                operation: operation.into(),
                expires: Instant::now() + Duration::from_secs(30),
            },
        );
        Ok(value)
    }
    fn execute(&mut self, request: Value) -> Result<Value, RacpError> {
        let operation = request["operation"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        if operation == "broker.status" {
            let mut status = self.native.status();
            status["ui_automation_available"] = json!(self.automation.is_some());
            status["input_ready"] =
                json!(status["available"] == true && status["input_hook_healthy"] == true);
            return Ok(status);
        }
        if operation == "broker.stop" {
            self.authority.abort();
            self.native.release_inputs()?;
            self.stopped = true;
            return Ok(json!({"stopped":true}));
        }
        let owner = request["context"]["owner_id"]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 96)
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        if request["context"]["device_id"] != self.device {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let scope = Scope {
            owner: owner.into(),
            device: self.device.clone(),
        };
        let op_id = request["context"]["operation_id"]
            .as_str()
            .filter(|s| s.starts_with("op_") && s.len() <= 96)
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        if operation == "broker.capture_read" || operation == "broker.capture_release" {
            let payload = &request["payload"];
            let id = payload["capture_id"]
                .as_str()
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let capture = self
                .captures
                .get(id)
                .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
            if capture.scope != scope
                || capture.operation != op_id
                || capture.expires <= Instant::now()
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            if operation == "broker.capture_release" {
                self.captures.remove(id);
                return Ok(json!({"released":true}));
            }
            let offset = payload["offset"]
                .as_u64()
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let length = payload["length"]
                .as_u64()
                .filter(|n| (1..=32768).contains(n))
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            if offset > capture.bytes.len() as u64 {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            let end = (offset + length).min(capture.bytes.len() as u64);
            return Ok(
                json!({"data":base64::engine::general_purpose::STANDARD.encode(&capture.bytes[offset as usize..end as usize]),"offset":offset,"next_offset":end,"eof":end==capture.bytes.len() as u64}),
            );
        }
        if !op_id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_')
        {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.native.cancelled = Some(self.pair_path.with_file_name(format!("cancel-{op_id}")));
        let payload = validate_operation(operation, request["payload"].clone())?;
        if payload["session_id"] != self.native.status()["session_id"] {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        if operation.starts_with("clipboard.") {
            return self.native.clipboard(operation, &payload);
        }
        let (snapshot, layout) = self.native.snapshot()?;
        let now = Instant::now();
        self.authority.tick(&snapshot, now);
        if self.authority.needs_release() {
            self.native.release_inputs()?;
            self.authority.cleanup_confirmed();
        }
        let deadline = now
            + Duration::from_millis(
                request["context"]["timeout_ms"]
                    .as_u64()
                    .unwrap_or(1000)
                    .min(30000),
            );
        if operation == "desktop.lease_acquire" {
            let id = self.authority.acquire(
                &scope,
                payload["ttl_ms"].as_u64().unwrap_or(15000),
                &snapshot,
                now,
            )?;
            return Ok(
                json!({"lease_id":id,"session_id":payload["session_id"],"ttl_ms":payload["ttl_ms"]}),
            );
        }
        if operation == "desktop.lease_renew" {
            let id = self.authority.renew(&scope, &payload, &snapshot, now)?;
            return Ok(
                json!({"lease_id":id,"session_id":payload["session_id"],"ttl_ms":payload["ttl_ms"]}),
            );
        }
        if operation == "desktop.lease_release" {
            self.authority.release(&scope, &payload, &snapshot, now)?;
            self.native.release_inputs()?;
            self.authority.cleanup_confirmed();
            return Ok(json!({"released":true}));
        }
        if matches!(
            operation,
            "desktop.activate"
                | "desktop.move"
                | "desktop.click"
                | "desktop.type"
                | "desktop.key"
                | "desktop.scroll"
                | "desktop.drag"
                | "desktop.invoke"
                | "desktop.set_value"
        ) {
            let activated = operation == "desktop.activate";
            self.authority
                .guard(&scope, &payload, &snapshot, now, activated)?;
            let dispatch = if matches!(operation, "desktop.invoke" | "desktop.set_value") {
                self.automation
                    .as_mut()
                    .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?
                    .mutate(&self.native, &scope, operation, &payload, deadline)
            } else {
                self.native
                    .dispatch_input(operation, &payload, &snapshot, deadline)
            };
            if dispatch.is_err() {
                self.authority.abort();
                self.native.release_inputs()?;
                self.authority.cleanup_confirmed();
            }
            let result = dispatch?;
            let (after, _) = self.native.snapshot()?;
            self.authority.dispatched(&after, activated)?;
            return Ok(result);
        }
        let observation = self.authority.observe(&scope, &snapshot, now)?;
        let mut result = layout;
        result["observation_id"] = json!(observation);
        result["captured_at"] = json!(timestamp());
        result["ttl_ms"] = json!(5000);
        match operation {
            "desktop.monitors" => (),
            "desktop.inspect" => {
                let value = self
                    .automation
                    .as_mut()
                    .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?
                    .inspect(
                        &self.native,
                        &scope,
                        payload["window_id"].as_str().unwrap_or(""),
                        result["observation_id"].as_str().unwrap(),
                        payload["limit"].as_u64().unwrap_or(64) as usize,
                        deadline,
                    )?;
                result
                    .as_object_mut()
                    .unwrap()
                    .extend(value.as_object().unwrap().clone());
            }
            "desktop.windows" => {
                let limit = payload["limit"].as_u64().unwrap_or(64) as usize;
                result["windows"] =
                    json!(snapshot.windows.values().take(limit).collect::<Vec<_>>());
                result["truncated"] = json!(snapshot.windows.len() > limit);
                while serde_json::to_vec(&result)?.len() > 60 * 1024 {
                    let values = result["windows"].as_array_mut().unwrap();
                    if values.is_empty() {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    values.pop();
                    result["truncated"] = json!(true);
                }
            }
            "desktop.foreground" => {
                result["window_id"] = json!(snapshot.foreground);
            }
            "desktop.screenshot" => {
                let (png, preview, capture_meta) =
                    self.native.capture(&payload, &snapshot, &result)?;
                let mut original = capture_meta;
                original
                    .as_object_mut()
                    .unwrap()
                    .extend(result.as_object().unwrap().clone());
                let id = self.capture_store(png, &scope, op_id)?;
                let original_id = id["capture_id"].as_str().unwrap().to_owned();
                original["capture"] = id;
                if let Some(bytes) = preview {
                    match self.capture_store(bytes, &scope, op_id) {
                        Ok(id) => original["preview"]["capture"] = id,
                        Err(e) => {
                            self.captures.remove(&original_id);
                            return Err(e);
                        }
                    }
                }
                result = original;
            }
            _ => return Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
        }
        Ok(result)
    }
}
