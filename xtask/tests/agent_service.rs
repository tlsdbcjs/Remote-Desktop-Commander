use serde_json::Value;
use std::process::Command;

#[test]
fn service_stage_plan_has_agent_role_and_separate_output() {
    let output = Command::new(env!("CARGO_BIN_EXE_xtask"))
        .args(["stage-agent-service", "--dry-run"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(plan["payload_kind"], "native-agent-service");
    assert_eq!(
        plan["base_files"],
        serde_json::json!(["LICENSE", "racp-agent.exe", "sbom.json"])
    );
    assert!(plan["output"]
        .as_str()
        .unwrap()
        .replace('\\', "/")
        .contains("/dist/agent/"));
    assert!(plan["output"]
        .as_str()
        .unwrap()
        .replace('\\', "/")
        .ends_with("/staging"));
    assert_eq!(plan["writes_files"], false);
}
#[test]
fn unsupported_service_targets_and_client_machine_artifacts_are_rejected() {
    for args in [
        vec!["stage-agent-service", "--dry-run", "--platform", "mac"],
        vec!["stage-agent-service", "--dry-run", "--arch", "arm64"],
        vec!["package", "--install-scope", "machine", "--dry-run"],
        vec!["package", "--install-scope", "both", "--dry-run"],
    ] {
        let output = Command::new(env!("CARGO_BIN_EXE_xtask"))
            .args(&args)
            .output()
            .unwrap();
        assert!(!output.status.success(), "admitted {args:?}");
    }
}

#[test]
fn service_package_plan_is_machine_only_and_never_executes_installation() {
    let output = Command::new(env!("CARGO_BIN_EXE_xtask"))
        .args(["package-agent-service", "--dry-run"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(plan["payload_kind"], "native-agent-service");
    assert_eq!(plan["install_scope"], "machine");
    assert_eq!(plan["artifact_execution"], false);
    assert_eq!(plan["writes_files"], false);
    assert_eq!(
        plan["formats"],
        serde_json::json!(["NSIS Agent service setup EXE"])
    );
}
