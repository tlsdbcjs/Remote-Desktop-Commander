//! Bounded, resumable transfers use scoped credentials and verified SHA-256.
use crate::http_client;
use futures_util::StreamExt;
use racp_contract::{digest, RacpError};
use racp_core::{gateway_origin, secure_append_file, secure_read_file, OutputSpool};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};
const MAX: u64 = 1024 * 1024 * 1024;
const CHUNK: u64 = 4 * 1024 * 1024;
#[derive(Clone)]
pub struct ArtifactClient {
    http: reqwest::Client,
    gateway: String,
    credential: String,
    device: String,
}
impl ArtifactClient {
    pub fn new(
        gateway: &str,
        credential: &str,
        device: &str,
        ca: Option<&Path>,
    ) -> Result<Self, RacpError> {
        Ok(Self {
            http: http_client(ca)?,
            gateway: gateway_origin(gateway)?,
            credential: credential.into(),
            device: device.into(),
        })
    }
    fn endpoint(&self, path: &str) -> String {
        format!("{}{}", self.gateway, path)
    }
    async fn checked(&self, response: reqwest::Response) -> Result<Value, RacpError> {
        let status = response.status();
        let mut body = vec![];
        let mut stream = response.bytes_stream();
        while let Some(next) = stream.next().await {
            let next = next.map_err(|_| RacpError::new("TRANSFER_INTERRUPTED"))?;
            if body.len() + next.len() > 32768 {
                return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
            }
            body.extend_from_slice(&next);
        }
        let value: Value = serde_json::from_slice(&body)
            .map_err(|_| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
        if !status.is_success() {
            return Err(RacpError::new(match value["error"]["code"].as_str() {
                Some("ARTIFACT_EXPIRED") => "ARTIFACT_EXPIRED",
                Some("ARTIFACT_NOT_FOUND") => "ARTIFACT_NOT_FOUND",
                Some("CHECKSUM_MISMATCH") => "CHECKSUM_MISMATCH",
                Some("PERMISSION_DENIED") => "PERMISSION_DENIED",
                Some("CONFLICT") => "CONFLICT",
                Some("RESOURCE_EXHAUSTED") => "RESOURCE_EXHAUSTED",
                Some("PRECONDITION_FAILED") => "PRECONDITION_FAILED",
                _ => "TRANSFER_INTERRUPTED",
            }));
        }
        if !value.is_object() {
            return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
        }
        Ok(value)
    }
    async fn call(
        &self,
        method: reqwest::Method,
        path: &str,
        credential: &str,
        body: Option<Value>,
    ) -> Result<Value, RacpError> {
        let mut request = self
            .http
            .request(method, self.endpoint(path))
            .bearer_auth(credential);
        if let Some(body) = body {
            request = request.json(&body);
        }
        let response = request
            .send()
            .await
            .map_err(|_| RacpError::new("TRANSFER_INTERRUPTED"))?;
        self.checked(response).await
    }
    async fn authorize(&self, id: &str) -> Result<Value, RacpError> {
        identifier(id)?;
        self.call(
            reqwest::Method::POST,
            &format!("/api/v1/artifact-transfers/{id}/authorize"),
            &self.credential,
            None,
        )
        .await
    }
    pub async fn upload(&self, output: &Value, outputs: &OutputSpool) -> Result<Value, RacpError> {
        let path = outputs.path(output)?;
        let (size, hash) = file_digest(path.clone()).await?;
        if size != output["size_bytes"].as_u64().unwrap_or(MAX + 1) || hash != output["sha256"] {
            return Err(RacpError::new("CHECKSUM_MISMATCH"));
        }
        let id = output["id"]
            .as_str()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
        let mut state = if let Some(transfer) = output["transfer_id"].as_str() {
            self.authorize(transfer).await?
        } else {
            self.call(reqwest::Method::POST,"/api/v1/artifact-transfers",&self.credential,Some(json!({"device_id":self.device,"operation_id":output["operation_id"],"output_id":id,"direction":"upload","size_bytes":size,"sha256":hash,"media_type":output["media_type"]}))).await?
        };
        check_scope(&state, &self.device, "upload", size, &hash)?;
        let transfer = state["id"]
            .as_str()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?
            .to_string();
        identifier(&transfer)?;
        outputs.set_transfer(id, &transfer)?;
        let deadline = tokio::time::Instant::now() + Duration::from_secs(600);
        let mut renewed = tokio::time::Instant::now();
        let mut file = tokio::fs::File::from_std(secure_read_file(&path)?);
        let mut offset = decimal(&state["committed_bytes"])
            .filter(|n| *n <= size)
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
        while offset < size {
            if tokio::time::Instant::now() >= deadline {
                return Err(RacpError::new("TIMEOUT"));
            }
            if renewed.elapsed() > Duration::from_secs(480) {
                state = self.authorize(&transfer).await?;
                check_scope(&state, &self.device, "upload", size, &hash)?;
                renewed = tokio::time::Instant::now();
            }
            file.seek(std::io::SeekFrom::Start(offset)).await?;
            let mut chunk = vec![0; CHUNK.min(size - offset) as usize];
            file.read_exact(&mut chunk)
                .await
                .map_err(|_| RacpError::new("PRECONDITION_FAILED"))?;
            let expected = offset + chunk.len() as u64;
            let mut committed = false;
            for attempt in 0..3 {
                let credential = state["credential"]
                    .as_str()
                    .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
                let response = self
                    .http
                    .put(self.endpoint(&format!("/api/v1/artifact-transfers/{transfer}/content")))
                    .bearer_auth(credential)
                    .header(
                        "Content-Range",
                        format!("bytes {offset}-{}/{size}", expected - 1),
                    )
                    .header("X-Chunk-SHA256", digest(&chunk))
                    .body(chunk.clone())
                    .send()
                    .await;
                match response {
                    Ok(response) if response.status().as_u16() == 410 => {
                        state = self.authorize(&transfer).await?;
                        check_scope(&state, &self.device, "upload", size, &hash)?;
                    }
                    Ok(response) => {
                        let progress = self.checked(response).await?;
                        if decimal(&progress["committed_bytes"]) != Some(expected) {
                            return Err(RacpError::new("PRECONDITION_FAILED"));
                        }
                        offset = expected;
                        committed = true;
                        break;
                    }
                    Err(_) => {
                        tokio::time::sleep(Duration::from_millis(250 * (attempt + 1))).await;
                        let progress = self
                            .call(
                                reqwest::Method::GET,
                                &format!("/api/v1/artifact-transfers/{transfer}"),
                                &self.credential,
                                None,
                            )
                            .await?;
                        if decimal(&progress["committed_bytes"]) == Some(expected) {
                            offset = expected;
                            committed = true;
                            break;
                        }
                        if decimal(&progress["committed_bytes"]) != Some(offset) {
                            return Err(RacpError::new("PRECONDITION_FAILED"));
                        }
                    }
                }
            }
            if !committed {
                return Err(RacpError::new("TRANSFER_INTERRUPTED"));
            }
        }
        for _ in 0..3 {
            let result = self
                .call(
                    reqwest::Method::POST,
                    &format!("/api/v1/artifact-transfers/{transfer}/complete"),
                    state["credential"]
                        .as_str()
                        .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?,
                    Some(json!({"size_bytes":size,"sha256":hash})),
                )
                .await;
            match result {
                Ok(metadata) => {
                    let artifact = metadata["id"]
                        .as_str()
                        .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
                    identifier(artifact)?;
                    if metadata["size_bytes"].as_u64() != Some(size) || metadata["sha256"] != hash {
                        return Err(RacpError::new("CHECKSUM_MISMATCH"));
                    }
                    outputs.completed(id, artifact)?;
                    return Ok(metadata);
                }
                Err(e) if e.code.0 == "ARTIFACT_EXPIRED" => {
                    state = self.authorize(&transfer).await?;
                    check_scope(&state, &self.device, "upload", size, &hash)?;
                }
                Err(e) if e.code.0 == "TRANSFER_INTERRUPTED" => {
                    tokio::time::sleep(Duration::from_millis(250)).await;
                }
                Err(e) => return Err(e),
            }
        }
        Err(RacpError::new("TRANSFER_INTERRUPTED"))
    }
    pub async fn download(
        &self,
        artifact: &str,
        path: &Path,
        operation: &str,
    ) -> Result<Value, RacpError> {
        identifier(artifact)?;
        let mut state=self.call(reqwest::Method::POST,"/api/v1/artifact-transfers",&self.credential,Some(json!({"device_id":self.device,"operation_id":operation,"direction":"download","artifact_id":artifact}))).await?;
        let transfer = state["id"]
            .as_str()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?
            .to_string();
        identifier(&transfer)?;
        let size = state["size_bytes"]
            .as_u64()
            .filter(|n| *n <= MAX)
            .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
        let hash = state["sha256"]
            .as_str()
            .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?
            .to_string();
        check_scope(&state, &self.device, "download", size, &hash)?;
        if state["artifact_id"] != artifact {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let etag = format!("\"{hash}\"");
        let deadline = tokio::time::Instant::now() + Duration::from_secs(600);
        for attempt in 0..4 {
            if tokio::time::Instant::now() >= deadline {
                return Err(RacpError::new("TIMEOUT"));
            }
            let mut file = tokio::fs::File::from_std(secure_append_file(path)?);
            let mut offset = file.metadata().await?.len();
            if offset > size {
                return Err(RacpError::new("CHECKSUM_MISMATCH"));
            }
            if offset == size {
                file.sync_all().await?;
                break;
            }
            let mut request = self
                .http
                .get(self.endpoint(&format!("/api/v1/artifact-transfers/{transfer}/content")))
                .bearer_auth(
                    state["credential"]
                        .as_str()
                        .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?,
                )
                .header("If-Range", &etag);
            if offset > 0 {
                request = request.header("Range", format!("bytes={offset}-"));
            }
            let response = request.send().await;
            let transfer_result = async {
                let response = response.map_err(|_| RacpError::new("TRANSFER_INTERRUPTED"))?;
                if response.status().as_u16() == 410 {
                    return Err(RacpError::new("ARTIFACT_EXPIRED"));
                }
                if !response.status().is_success() {
                    return self.checked(response).await.map(|_| ());
                }
                if response.status().as_u16() != if offset > 0 { 206 } else { 200 }
                    || response.headers().get("etag").and_then(|v| v.to_str().ok())
                        != Some(etag.as_str())
                {
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
                if offset > 0
                    && response
                        .headers()
                        .get("content-range")
                        .and_then(|v| v.to_str().ok())
                        != Some(format!("bytes {offset}-{}/{size}", size - 1).as_str())
                {
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
                let mut stream = response.bytes_stream();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.map_err(|_| RacpError::new("TRANSFER_INTERRUPTED"))?;
                    offset = offset
                        .checked_add(chunk.len() as u64)
                        .filter(|n| *n <= size)
                        .ok_or_else(|| RacpError::new("CHECKSUM_MISMATCH"))?;
                    file.write_all(&chunk).await?;
                }
                file.sync_all().await?;
                if offset != size {
                    return Err(RacpError::new("TRANSFER_INTERRUPTED"));
                }
                Ok(())
            }
            .await;
            match transfer_result {
                Ok(()) => break,
                Err(e)
                    if attempt < 3
                        && ["TRANSFER_INTERRUPTED", "ARTIFACT_EXPIRED"].contains(&e.code.0) =>
                {
                    state = self.authorize(&transfer).await?;
                    check_scope(&state, &self.device, "download", size, &hash)?;
                    tokio::time::sleep(Duration::from_millis(250)).await;
                }
                Err(e) => return Err(e),
            }
        }
        let (actual_size, actual_hash) = file_digest(path.to_path_buf()).await?;
        if actual_size != size || actual_hash != hash {
            return Err(RacpError::new("CHECKSUM_MISMATCH"));
        }
        for _ in 0..3 {
            match self
                .call(
                    reqwest::Method::POST,
                    &format!("/api/v1/artifact-transfers/{transfer}/complete"),
                    state["credential"]
                        .as_str()
                        .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?,
                    Some(json!({"size_bytes":size,"sha256":hash})),
                )
                .await
            {
                Ok(_) => {
                    return Ok(
                        json!({"artifact_id":artifact,"sha256":hash,"size_bytes":size,"transfer_id":transfer}),
                    )
                }
                Err(e) if e.code.0 == "ARTIFACT_EXPIRED" => {
                    state = self.authorize(&transfer).await?;
                    check_scope(&state, &self.device, "download", size, &hash)?;
                }
                Err(e) if e.code.0 == "TRANSFER_INTERRUPTED" => {
                    tokio::time::sleep(Duration::from_millis(250)).await;
                }
                Err(e) => return Err(e),
            }
        }
        Err(RacpError::new("TRANSFER_INTERRUPTED"))
    }
}
fn identifier(s: &str) -> Result<(), RacpError> {
    if s.is_empty()
        || s.len() > 96
        || !s
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
    }
    Ok(())
}
fn check_scope(
    v: &Value,
    device: &str,
    direction: &str,
    size: u64,
    hash: &str,
) -> Result<(), RacpError> {
    if v["device_id"] != device
        || v["direction"] != direction
        || v["size_bytes"].as_u64() != Some(size)
        || v["sha256"] != hash
        || hash.len() != 64
        || !hash
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}
pub async fn file_digest(path: PathBuf) -> Result<(u64, String), RacpError> {
    tokio::task::spawn_blocking(move || {
        use std::io::Read;
        let mut file = secure_read_file(&path)?;
        let mut bytes = [0; 65536];
        let mut hash = Sha256::new();
        let mut size = 0u64;
        loop {
            let n = file.read(&mut bytes)?;
            if n == 0 {
                break;
            }
            size += n as u64;
            if size > MAX {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            hash.update(&bytes[..n]);
        }
        Ok((size, format!("{:x}", hash.finalize())))
    })
    .await
    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
}

fn decimal(value: &Value) -> Option<u64> {
    let text = value.as_str()?;
    if text.is_empty() || text.len() > 20 || !text.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    text.parse().ok()
}
