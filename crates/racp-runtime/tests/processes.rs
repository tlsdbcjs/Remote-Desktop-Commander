mod support;
use racp_core::AgentSettings;
use racp_runtime::providers::{Processes, Provider};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
fn setup() -> (tempfile::TempDir, Processes) {
    let root = tempfile::tempdir().unwrap();
    let settings:AgentSettings=serde_json::from_value(json!({"version":1,"gateway":"http://localhost:1234","device_id":"dev_1","workspace":root.path(),"data_dir":root.path().join("data"),"profile":"trusted_personal","allowed_workspaces":[],"ca_file":null,"desktop_enabled":false})).unwrap();
    let provider = Processes::new(&settings, "boot_test").unwrap();
    (root, provider)
}
async fn execute(
    provider: &Processes,
    operation: &str,
    payload: Value,
    principal: &str,
    timeout: u64,
) -> Value {
    provider.execute(json!({"operation":operation,"payload":racp_contract::validate_operation(operation,payload).unwrap(),"operation_id":racp_contract::new_id("op"),"agent_boot_id":"boot_test","remaining_timeout_ms":timeout,"context":{"workspace_id":"default","principal_id":principal}}),CancellationToken::new()).await.unwrap()
}
async fn spawn(provider: &Processes) -> Value {
    let result=execute(provider,"process.spawn",json!({"argv":[std::env::current_exe().unwrap(),"--ignored","--exact","support::fixture_child","--nocapture"],"env":{"FIXTURE_MODE":"sleep"}}),"owner_one",5000).await;
    assert_eq!(result["state"], "SUCCEEDED");
    result["result"].clone()
}
fn target(result: &Value) -> Value {
    json!({"pid":result["pid"],"create_time":result["create_time"],"agent_boot_id":"boot_test"})
}
#[tokio::test]
async fn managed_identity_and_principal_are_fenced_before_kill() {
    let (_root, provider) = setup();
    let child = spawn(&provider).await;
    let mut p = target(&child);
    p["create_time"] = json!(child["create_time"].as_f64().unwrap() + 1.0);
    p["force"] = json!(true);
    let result = execute(&provider, "process.terminate", p, "owner_one", 5000).await;
    assert_eq!(result["error"]["code"], "PRECONDITION_FAILED");
    let mut p = target(&child);
    p["force"] = json!(true);
    let denied = execute(&provider, "process.terminate", p.clone(), "owner_two", 5000).await;
    assert_eq!(denied["error"]["code"], "PERMISSION_DENIED");
    let killed = execute(&provider, "process.terminate", p, "owner_one", 5000).await;
    assert_eq!(killed["result"]["cleanup_status"], "complete");
    assert_eq!(killed["result"]["method"], "owned_tree_kill");
    assert_eq!(provider.inventory()[0]["state"], "CLOSED");
    provider.cleanup().await.unwrap();
}
#[tokio::test]
async fn wait_timeout_preserves_target_and_shutdown_cleans_owned_process() {
    let (_root, provider) = setup();
    let child = spawn(&provider).await;
    let waited = execute(&provider, "process.wait", target(&child), "owner_one", 150).await;
    assert_eq!(waited["state"], "TIMED_OUT");
    assert_eq!(waited["error"]["details"]["target_terminated"], false);
    let observed = execute(
        &provider,
        "process.inspect",
        json!({"pid":child["pid"]}),
        "owner_one",
        5000,
    )
    .await;
    assert_eq!(observed["result"]["handle"]["id"], child["handle"]["id"]);
    provider.cleanup().await.unwrap();
    let system = sysinfo::System::new_all();
    let pid = sysinfo::Pid::from_u32(child["pid"].as_u64().unwrap() as u32);
    assert!(system
        .process(pid)
        .is_none_or(|p| p.status() == sysinfo::ProcessStatus::Zombie));
}
#[tokio::test]
async fn process_snapshot_cursor_is_stable_and_limit_scope_is_enforced() {
    let (_root, provider) = setup();
    let first = execute(
        &provider,
        "process.list",
        json!({"limit":1}),
        "owner_one",
        5000,
    )
    .await;
    assert_eq!(first["state"], "SUCCEEDED");
    assert_eq!(first["result"]["items"].as_array().unwrap().len(), 1);
    let cursor = first["result"]["next_cursor"].clone();
    let second = execute(
        &provider,
        "process.list",
        json!({"limit":1,"cursor":cursor}),
        "owner_one",
        5000,
    )
    .await;
    assert_eq!(
        second["result"]["snapshot_id"],
        first["result"]["snapshot_id"]
    );
    assert_ne!(
        second["result"]["items"][0]["pid"],
        first["result"]["items"][0]["pid"]
    );
    let bad = execute(
        &provider,
        "process.list",
        json!({"limit":2,"cursor":cursor}),
        "owner_one",
        5000,
    )
    .await;
    assert_eq!(bad["error"]["code"], "CURSOR_EXPIRED");
}
