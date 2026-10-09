mod support;
use racp_core::AgentSettings;
use racp_runtime::providers::{Provider, Terminal, TerminalBuffer};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
#[test]
fn byte_cursor_preserves_partial_utf8_and_reports_eviction() {
    let mut buffer = TerminalBuffer::new(8);
    buffer.append("한".as_bytes());
    let result = buffer.read(0, 2, false).unwrap();
    assert_eq!(result["data"], "");
    assert_eq!(result["next_cursor"], "0");
    assert_eq!(result["required_min_bytes"], 4);
    assert_eq!(buffer.read(0, 3, false).unwrap()["data"], "한");
    buffer.append(b"\xff\xf0\x9f");
    let result = buffer.read(3, 3, false).unwrap();
    assert_eq!(result["data"], "\u{fffd}");
    assert_eq!(result["next_cursor"], "4");
    assert_eq!(result["invalid_byte_replacements"], 1);
    let final_read = buffer.read(4, 2, true).unwrap();
    assert_eq!(final_read["data"], "\u{fffd}");
    assert_eq!(final_read["eof"], true);
    buffer.append(b"1234567890");
    let expired = buffer.read(0, 8, false).unwrap_err();
    assert_eq!(expired["code"], "CURSOR_EXPIRED");
    assert_eq!(expired["details"]["earliest_cursor"], "8");
    assert_eq!(expired["details"]["lost_bytes"], "8");
}
fn setup() -> (tempfile::TempDir, Terminal) {
    let root = tempfile::tempdir().unwrap();
    let settings:AgentSettings=serde_json::from_value(json!({"version":1,"gateway":"http://localhost:1234","device_id":"dev_1","workspace":root.path(),"data_dir":root.path().join("data"),"profile":"trusted_personal","allowed_workspaces":[],"ca_file":null,"desktop_enabled":false})).unwrap();
    let provider = Terminal::new(&settings, "boot_test").unwrap();
    (root, provider)
}
async fn execute(provider: &Terminal, operation: &str, payload: Value, owner: &str) -> Value {
    provider.execute(json!({"operation":operation,"payload":racp_contract::validate_operation(operation,payload).unwrap(),"operation_id":racp_contract::new_id("op"),"agent_boot_id":"boot_test","remaining_timeout_ms":5000,"context":{"workspace_id":"default","principal_id":owner}}),CancellationToken::new()).await.unwrap()
}
async fn open(provider: &Terminal) -> Value {
    let result=execute(provider,"terminal.open",json!({"argv":[std::env::current_exe().unwrap(),"--ignored","--exact","support::fixture_child","--nocapture"],"env":{"FIXTURE_MODE":"terminal"},"cols":80,"rows":25}),"owner_one").await;
    assert_eq!(result["state"], "SUCCEEDED");
    result["result"].clone()
}
#[tokio::test]
async fn pty_owner_boot_input_resize_and_complete_close() {
    let (_root, provider) = setup();
    let session = open(&provider).await;
    let id = session["handle_id"].clone();
    let denied = execute(
        &provider,
        "terminal.write",
        json!({"handle_id":id,"data":"foreign\n"}),
        "owner_two",
    )
    .await;
    assert_eq!(denied["error"]["code"], "PERMISSION_DENIED");
    let stale = execute(
        &provider,
        "terminal.read",
        json!({"handle_id":id,"agent_boot_id":"old_boot"}),
        "owner_one",
    )
    .await;
    assert_eq!(stale["error"]["code"], "HANDLE_EXPIRED");
    let input = if cfg!(windows) {
        "hello 한글\r"
    } else {
        "hello 한글\n"
    };
    let write = execute(
        &provider,
        "terminal.write",
        json!({"handle_id":id,"data":input}),
        "owner_one",
    )
    .await;
    assert_eq!(write["result"]["accepted_bytes"], input.len());
    let mut output = String::new();
    let mut cursor = "0".to_owned();
    for _ in 0..15 {
        let read = execute(
            &provider,
            "terminal.read",
            json!({"handle_id":id,"cursor":cursor,"wait_ms":300}),
            "owner_one",
        )
        .await;
        assert_eq!(read["state"], "SUCCEEDED");
        output.push_str(read["result"]["data"].as_str().unwrap());
        cursor = read["result"]["next_cursor"].as_str().unwrap().into();
        if output.contains("response=hello 한글") {
            break;
        }
    }
    assert!(output.contains("response=hello 한글"), "{output}");
    let resized = execute(
        &provider,
        "terminal.resize",
        json!({"handle_id":id,"cols":100,"rows":30}),
        "owner_one",
    )
    .await;
    assert_eq!(resized["result"]["cols"], 100);
    assert_eq!(resized["result"]["handle"]["resource_revision"], "2");
    let closed = execute(
        &provider,
        "terminal.close",
        json!({"handle_id":id}),
        "owner_one",
    )
    .await;
    assert_eq!(closed["result"]["cleanup_status"], "complete");
    assert_eq!(closed["result"]["state"], "CLOSED");
    provider.cleanup().await.unwrap();
}
