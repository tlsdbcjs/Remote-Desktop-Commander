use racp_contract::{decode_bridge, decode_message, validate_operation};
use serde_json::json;
#[test]
fn bridge_bounds_and_unknown_fields() {
    assert_eq!(
        decode_bridge(br#"{"action":"info"}"#).unwrap(),
        json!({"action":"info"})
    );
    assert!(decode_bridge(&vec![b' '; 16385]).is_err());
    assert!(decode_bridge(br#"{"action":"info","token":"secret"}"#).is_err());
    assert!(decode_bridge(br#"{"action":"exec"}"#).is_err());
}
#[test]
fn shell_mode_and_bounds() {
    let valid = validate_operation("shell.exec", json!({"argv":["echo","한글"]})).unwrap();
    assert_eq!(valid["mode"], "argv");
    assert!(validate_operation(
        "shell.exec",
        json!({"argv":["echo"],"command":"echo","shell":"bash"})
    )
    .is_err());
    assert!(validate_operation("shell.exec", json!({"argv":["x\u{0000}"]})).is_err());
    assert!(validate_operation("shell.exec", json!({"argv":vec!["x";257]})).is_err());
    assert!(validate_operation("shell.exec", json!({"mode":"shell","command":"echo"})).is_err());
}
#[test]
fn wire_limits() {
    let hello = json!({"type":"hello","protocol":1,"device_id":"dev_1","agent_boot_id":"boot_1","agent_version":"0.1.11","supported_protocols":[1],"platform":"Linux","architecture":"x86_64","execution_identity":"test","capabilities":[]});
    assert_eq!(
        decode_message(&serde_json::to_vec(&hello).unwrap()).unwrap()["type"],
        "hello"
    );
    let mut invalid = hello;
    invalid["protocol"] = json!(true);
    assert!(decode_message(&serde_json::to_vec(&invalid).unwrap()).is_err());
    assert!(decode_message(&vec![b' '; 1024 * 1024 + 1]).is_err());
}
#[test]
fn registry_coverage_and_python_normalization() {
    let fixtures: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/operations.json")).unwrap();
    assert_eq!(fixtures.as_array().unwrap().len(), 78);
    for f in fixtures.as_array().unwrap() {
        assert_eq!(
            validate_operation(f["operation"].as_str().unwrap(), f["input"].clone()).unwrap(),
            f["expected"],
            "{}",
            f["operation"]
        );
    }
}
#[test]
fn relational_validation() {
    for (op, input) in [
        (
            "filesystem.write",
            json!({"path":"x","content":"x","artifact_id":"a"}),
        ),
        (
            "filesystem.write",
            json!({"path":"x","content":"x","mode":"replace"}),
        ),
        (
            "filesystem.write",
            json!({"path":"x","content":"x","mode":"append"}),
        ),
        (
            "browser.click",
            json!({"browser_id":"b","page_id":"p","observation_id":"o","navigation_revision":"1"}),
        ),
        (
            "browser.navigate",
            json!({"browser_id":"b","page_id":"p","url":"file:///secret"}),
        ),
        (
            "browser.attach",
            json!({"endpoint_url":"http://example.org:9222"}),
        ),
        (
            "desktop.screenshot",
            json!({"session_id":1,"window_id":"w","monitor_id":"m"}),
        ),
        (
            "desktop.key",
            json!({"session_id":1,"lease_id":"l","keys":["CTRL","CTRL"]}),
        ),
        (
            "debugger.memory",
            json!({"debug_id":"d","address":"0xffffffffffffffff","size_bytes":2}),
        ),
    ] {
        assert!(validate_operation(op, input).is_err(), "{op}");
    }
}
#[test]
fn strict_integer_and_terminal_bytes() {
    let mut hello = serde_json::json!({"type":"hello","protocol":1.0,"device_id":"d","agent_boot_id":"b","agent_version":"0.1.11","supported_protocols":[1],"platform":"Linux","architecture":"x64","execution_identity":"u","capabilities":[]});
    assert!(decode_message(&serde_json::to_vec(&hello).unwrap()).is_err());
    hello["protocol"] = json!(1);
    assert!(decode_message(&serde_json::to_vec(&hello).unwrap()).is_ok());
    assert!(validate_operation(
        "terminal.write",
        json!({"handle_id":"h","data":"@","encoding":"base64"})
    )
    .is_err());
    assert!(validate_operation(
        "terminal.write",
        json!({"handle_id":"h","data":"한".repeat(22000)})
    )
    .is_err());
    let env: serde_json::Map<String, serde_json::Value> =
        (0..256).map(|i| (format!("key{i}"), json!("x"))).collect();
    assert!(validate_operation("terminal.open", json!({"env":env})).is_ok());
}
