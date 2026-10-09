//! Development-only transfer harness. No credentials appear in arguments/logs.
use racp_contract::RacpError;
use racp_core::{Journal, OutputSpool};
use racp_runtime::artifacts::ArtifactClient;
use serde_json::{json, Value};
use std::{io::Read, path::PathBuf};
#[tokio::main]
async fn main() {
    let result = run().await;
    match result {
        Ok(value) => println!("{}", json!({"ok":true,"result":value})),
        Err(error) => println!("{}", json!({"ok":false,"code":error.code})),
    }
}
async fn run() -> Result<Value, RacpError> {
    let mut raw = vec![];
    std::io::stdin().take(16385).read_to_end(&mut raw)?;
    if raw.len() > 16384 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let input: Value = serde_json::from_slice(&raw)?;
    let text = |key: &str| {
        input[key]
            .as_str()
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))
    };
    let client = ArtifactClient::new(
        text("gateway")?,
        text("credential")?,
        text("device_id")?,
        Some(std::path::Path::new(text("ca")?)),
    )?;
    let path = PathBuf::from(text("path")?);
    if text("action")? == "upload" {
        let journal = Journal::open(std::path::Path::new(text("db")?))?;
        let outputs = OutputSpool::new(
            path.parent()
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?
                .into(),
            journal,
        )?;
        let mut output =
            outputs.register(&path, text("operation_id")?, "application/octet-stream")?;
        if let Some(transfer) = input["transfer_id"].as_str() {
            outputs.set_transfer(output["id"].as_str().unwrap(), transfer)?;
            output = outputs.get(output["id"].as_str().unwrap())?;
        }
        client.upload(&output, &outputs).await
    } else {
        client
            .download(text("artifact_id")?, &path, text("operation_id")?)
            .await
    }
}
