#![cfg(all(windows, target_arch = "x86_64"))]
mod support;
use racp_core::AgentSettings;
use racp_runtime::providers::{Processes, Provider};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    io::{BufRead, BufReader},
    process::{Child, Command, Stdio},
};
use tokio_util::sync::CancellationToken;
struct Fixture(Child);
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn setup() -> (tempfile::TempDir, Processes) {
    let root = tempfile::tempdir().unwrap();
    let settings:AgentSettings=serde_json::from_value(json!({"version":1,"gateway":"http://localhost:1234","device_id":"dev_memory","workspace":root.path(),"data_dir":root.path().join("data"),"profile":"trusted_personal","allowed_workspaces":[],"ca_file":null,"desktop_enabled":false})).unwrap();
    let provider = Processes::new(&settings, "boot_memory").unwrap();
    (root, provider)
}
fn fixture() -> (Fixture, Value) {
    let mut child = Fixture(
        Command::new(std::env::current_exe().unwrap())
            .args([
                "--ignored",
                "--exact",
                "support::fixture_child",
                "--nocapture",
            ])
            .env("FIXTURE_MODE", "memory")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap(),
    );
    let stdout = child.0.stdout.take().unwrap();
    let value = BufReader::new(stdout)
        .lines()
        .find_map(|line| {
            line.unwrap()
                .strip_prefix("memory_fixture=")
                .map(|s| serde_json::from_str::<Value>(s).unwrap())
        })
        .unwrap();
    (child, value)
}
async fn execute(provider: &Processes, operation: &str, payload: Value) -> Value {
    provider.execute(json!({"operation":operation,"payload":racp_contract::validate_operation(operation,payload).unwrap(),"operation_id":racp_contract::new_id("op"),"agent_boot_id":"boot_memory","remaining_timeout_ms":5000,"context":{"workspace_id":"default","principal_id":"owner_memory"}}),CancellationToken::new()).await.unwrap()
}
async fn identity(provider: &Processes, pid: u32) -> Value {
    let info = execute(provider, "process.inspect", json!({"pid":pid})).await;
    assert_eq!(info["state"], "SUCCEEDED", "{info}");
    json!({"pid":pid,"create_time":info["result"]["create_time"],"agent_boot_id":"boot_memory"})
}
#[tokio::test]
async fn memory_native_regions_inline_and_large_verified_bytes() {
    let (_root, provider) = setup();
    let (child, data) = fixture();
    let target = identity(&provider, child.0.id()).await;
    let mut p = target.clone();
    p["start_address"] = data["address"].clone();
    p["limit"] = json!(1);
    let regions = execute(&provider, "process.memory_regions", p).await;
    assert_eq!(regions["state"], "SUCCEEDED", "{regions}");
    assert_eq!(regions["result"]["items"][0]["readable"], true);
    let mut p = target.clone();
    p["address"] = data["address"].clone();
    p["size_bytes"] = json!(64);
    let inline = execute(&provider, "process.memory_read", p.clone()).await;
    assert_eq!(inline["state"], "SUCCEEDED", "{inline}");
    let expected: Vec<u8> = (0..64).collect();
    assert_eq!(
        inline["result"]["bytes_hex"],
        expected
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    assert_eq!(
        inline["result"]["sha256"],
        format!("{:x}", Sha256::digest(&expected))
    );
    assert_eq!(inline["result"]["atomic_snapshot"], false);
    p["size_bytes"] = data["size_bytes"].clone();
    let large = execute(&provider, "process.memory_read", p.clone()).await;
    assert_eq!(large["state"], "SUCCEEDED", "{large}");
    let bytes = std::fs::read(large["result"]["spool_path"].as_str().unwrap()).unwrap();
    assert_eq!(
        bytes,
        (0..128 * 1024).map(|i| (i % 256) as u8).collect::<Vec<_>>()
    );
    assert_eq!(
        large["result"]["sha256"],
        format!("{:x}", Sha256::digest(&bytes))
    );
    p["address"] = json!("0x1");
    let invalid = execute(&provider, "process.memory_read", p).await;
    assert_eq!(invalid["error"]["code"], "MEMORY_UNAVAILABLE");
    provider.cleanup().await.unwrap();
}
#[tokio::test]
async fn memory_native_rejects_agent_pid_and_wrong_birth_or_boot() {
    let (_root, provider) = setup();
    let (child, data) = fixture();
    let mut p = identity(&provider, child.0.id()).await;
    p["address"] = data["address"].clone();
    p["size_bytes"] = json!(64);
    let birth = p["create_time"].as_f64().unwrap();
    p["create_time"] = json!(birth + 1.0);
    assert_eq!(
        execute(&provider, "process.memory_read", p.clone()).await["error"]["code"],
        "PRECONDITION_FAILED"
    );
    p["create_time"] = json!(birth);
    p["agent_boot_id"] = json!("boot_stale");
    assert_eq!(
        execute(&provider, "process.memory_read", p).await["error"]["code"],
        "PRECONDITION_FAILED"
    );
    let mut own = identity(&provider, std::process::id()).await;
    own["address"] = json!("0x1");
    own["size_bytes"] = json!(64);
    assert_eq!(
        execute(&provider, "process.memory_read", own).await["error"]["code"],
        "PERMISSION_DENIED"
    );
    provider.cleanup().await.unwrap();
}
