mod support;
use racp_contract::validate_operation;
use racp_core::AgentSettings;
use racp_runtime::providers::{Provider, Shell};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
fn setup() -> (tempfile::TempDir, Shell) {
    let root = tempfile::tempdir().unwrap();
    let settings:AgentSettings=serde_json::from_value(json!({"version":1,"gateway":"http://localhost:1234","device_id":"dev_1","workspace":root.path(),"data_dir":root.path().join("data"),"profile":"trusted_personal","allowed_workspaces":[],"ca_file":null,"desktop_enabled":false})).unwrap();
    let shell = Shell::new(&settings).unwrap();
    (root, shell)
}
fn payload(mode: &str) -> Value {
    json!({"mode":"argv","argv":[std::env::current_exe().unwrap(),"--ignored","--exact","support::fixture_child","--nocapture"],"env":{"FIXTURE_MODE":mode},"max_output_bytes":2048})
}
async fn execute(shell: &Shell, payload: Value, cancel: CancellationToken, timeout: u64) -> Value {
    shell.execute(json!({"operation":"shell.exec","payload":validate_operation("shell.exec",payload).unwrap(),"operation_id":racp_contract::new_id("op"),"remaining_timeout_ms":timeout,"context":{"workspace_id":"default"}}),cancel).await.unwrap()
}
#[tokio::test]
async fn argv_unicode_exit_and_sanitized_working_directory() {
    let (root, shell) = setup();
    let result = execute(&shell, payload("echo"), CancellationToken::new(), 5000).await;
    assert_eq!(result["state"], "SUCCEEDED");
    assert_eq!(result["result"]["exit_code"], 7);
    assert!(result["result"]["stdout"]
        .as_str()
        .unwrap()
        .contains("unicode 한글"));
    assert!(result["result"]["stdout"]
        .as_str()
        .unwrap()
        .contains(root.path().to_str().unwrap()));
    assert!(result["result"]["stderr"]
        .as_str()
        .unwrap()
        .contains("stderr marker"));
    assert_eq!(result["result"]["cleanup_status"], "complete");
}
#[tokio::test]
async fn protected_environment_and_workspace_are_rejected_before_launch() {
    let (root, shell) = setup();
    let mut p = payload("echo");
    p["env"] = json!({"PATH":"injected"});
    let result = execute(&shell, p, CancellationToken::new(), 5000).await;
    assert_eq!(result["error"]["code"], "PERMISSION_DENIED");
    let mut p = payload("echo");
    p["cwd"] = json!(root.path().parent().unwrap());
    let result = execute(&shell, p, CancellationToken::new(), 5000).await;
    assert_eq!(result["error"]["code"], "PATH_ACCESS_DENIED");
}
#[tokio::test]
async fn large_output_has_bounded_preview_and_lossless_channel_frames() {
    let (_root, shell) = setup();
    let result = execute(&shell, payload("spam"), CancellationToken::new(), 5000).await;
    assert_eq!(result["state"], "SUCCEEDED");
    assert_eq!(result["result"]["truncated"], true);
    assert!(result["result"]["stdout"].as_str().unwrap().len() <= 1024);
    let bytes = std::fs::read(result["result"]["spool_path"].as_str().unwrap()).unwrap();
    let (mut pos, mut count) = (0, 0);
    while pos < bytes.len() {
        assert!(bytes[pos] <= 1);
        let size = u32::from_be_bytes(bytes[pos + 1..pos + 5].try_into().unwrap()) as usize;
        pos += 5;
        count += size;
        pos += size;
    }
    assert_eq!(pos, bytes.len());
    assert_eq!(
        count as u64,
        result["result"]["spooled_bytes"].as_u64().unwrap()
    );
    assert!(count >= 1024 * 1024);
}
#[tokio::test]
async fn cancellation_and_parent_exit_reap_inherited_pipe_descendants() {
    let (_root, shell) = setup();
    let cancel = CancellationToken::new();
    let signal = cancel.clone();
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        signal.cancel();
    });
    let result = execute(&shell, payload("sleep"), cancel, 5000).await;
    assert_eq!(result["state"], "CANCELLED");
    assert_eq!(result["result"]["cleanup_status"], "complete");
    let start = std::time::Instant::now();
    let result = execute(&shell, payload("tree"), CancellationToken::new(), 5000).await;
    assert_eq!(result["state"], "SUCCEEDED");
    assert!(start.elapsed() < std::time::Duration::from_secs(3));
    assert_eq!(result["result"]["cleanup_status"], "complete");
    let text = result["result"]["stdout"].as_str().unwrap();
    let pid = text
        .split("child_pid=")
        .nth(1)
        .unwrap()
        .split_whitespace()
        .next()
        .unwrap()
        .parse::<u32>()
        .unwrap();
    let system = sysinfo::System::new_all();
    assert!(system
        .process(sysinfo::Pid::from_u32(pid))
        .is_none_or(|p| p.status() == sysinfo::ProcessStatus::Zombie));
}
