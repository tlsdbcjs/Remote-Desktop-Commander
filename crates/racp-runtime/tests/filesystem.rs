use racp_contract::validate_operation;
use racp_core::AgentSettings;
use racp_runtime::providers::{Filesystem, Provider};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
fn provider(root: &std::path::Path) -> Filesystem {
    let workspace = root.join("workspace");
    let docs = root.join("docs");
    std::fs::create_dir(&workspace).unwrap();
    std::fs::create_dir(&docs).unwrap();
    let settings:AgentSettings=serde_json::from_value(json!({"version":1,"gateway":"http://localhost:1234","device_id":"dev_1","workspace":workspace,"data_dir":root.join("data"),"profile":"trusted_personal","allowed_workspaces":[{"id":"docs","path":docs}],"ca_file":null,"desktop_enabled":false})).unwrap();
    Filesystem::new(&settings).unwrap()
}
async fn execute(files: &Filesystem, operation: &str, payload: Value) -> Value {
    files.execute(json!({"operation":operation,"payload":validate_operation(operation,payload).unwrap(),"operation_id":racp_contract::new_id("op"),"remaining_timeout_ms":10000,"context":{"workspace_id":"default"}}),CancellationToken::new()).await.unwrap()
}
#[tokio::test]
async fn write_create_cas_append_and_preserve_newline_bom() {
    let root = tempfile::tempdir().unwrap();
    let files = provider(root.path());
    let target = root.path().join("workspace/fixture.txt");
    std::fs::write(&target, b"\xef\xbb\xbfline\r\n").unwrap();
    let hash = racp_contract::digest(std::fs::read(&target).unwrap());
    let rejected=execute(&files,"filesystem.write",json!({"path":"fixture.txt","mode":"replace","overwrite":true,"content":"new","expected_sha256":"0".repeat(64)})).await;
    assert_eq!(rejected["error"]["code"], "PRECONDITION_FAILED");
    assert_eq!(racp_contract::digest(std::fs::read(&target).unwrap()), hash);
    let result=execute(&files,"filesystem.write",json!({"path":"fixture.txt","mode":"replace","overwrite":true,"content":"first\nsecond\n","expected_sha256":hash})).await;
    assert_eq!(result["state"], "SUCCEEDED");
    assert_eq!(
        std::fs::read(&target).unwrap(),
        b"\xef\xbb\xbffirst\r\nsecond\r\n"
    );
    let result = execute(
        &files,
        "filesystem.write",
        json!({"path":"fixture.txt","mode":"append","expected_offset":"0","content":"bad"}),
    )
    .await;
    assert_eq!(result["error"]["code"], "PRECONDITION_FAILED");
    let size = target.metadata().unwrap().len();
    let result=execute(&files,"filesystem.write",json!({"path":"fixture.txt","mode":"append","expected_offset":size.to_string(),"content":"third\n"})).await;
    assert_eq!(result["result"]["atomic"], false);
    assert!(std::fs::read(&target).unwrap().ends_with(b"third\r\n"));
    let result = execute(
        &files,
        "filesystem.write",
        json!({"path":"fixture.txt","content":"collision"}),
    )
    .await;
    assert_eq!(result["error"]["code"], "CONFLICT");
}
#[tokio::test]
async fn workspace_escape_and_explicit_cross_folder_copy() {
    let root = tempfile::tempdir().unwrap();
    let files = provider(root.path());
    std::fs::write(root.path().join("workspace/source.bin"), b"source").unwrap();
    let escaped = execute(&files, "filesystem.read", json!({"path":"../outside"})).await;
    assert_eq!(escaped["error"]["code"], "PATH_ACCESS_DENIED");
    let result = execute(
        &files,
        "filesystem.copy",
        json!({"source":"source.bin","destination":"copied.bin","destination_workspace_id":"docs"}),
    )
    .await;
    assert_eq!(result["state"], "SUCCEEDED");
    assert_eq!(
        std::fs::read(root.path().join("docs/copied.bin")).unwrap(),
        b"source"
    );
    let denied = execute(
        &files,
        "filesystem.delete",
        json!({"path":".","recursive":true,"expected_revision":"ignored"}),
    )
    .await;
    assert_eq!(denied["error"]["code"], "PATH_ACCESS_DENIED");
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(root.path().join("docs"), root.path().join("workspace/link"))
            .unwrap();
        let result = execute(&files, "filesystem.read", json!({"path":"link/copied.bin"})).await;
        assert_eq!(result["error"]["code"], "PATH_ACCESS_DENIED");
    }
}
#[tokio::test]
async fn listing_cursor_scope_revision_and_recursive_delete() {
    let root = tempfile::tempdir().unwrap();
    let files = provider(root.path());
    for name in ["a", "b", "c"] {
        std::fs::write(root.path().join("workspace").join(name), name).unwrap();
    }
    let first = execute(&files, "filesystem.list", json!({"path":".","limit":1})).await;
    assert_eq!(first["result"]["items"].as_array().unwrap().len(), 1);
    let cursor = first["result"]["next_cursor"].clone();
    assert!(cursor.is_string());
    let second = execute(
        &files,
        "filesystem.list",
        json!({"path":".","limit":1,"cursor":cursor}),
    )
    .await;
    assert_eq!(second["result"]["items"][0]["name"], "b");
    std::fs::write(root.path().join("workspace/d"), b"new").unwrap();
    let stale = execute(
        &files,
        "filesystem.list",
        json!({"path":".","limit":1,"cursor":cursor}),
    )
    .await;
    assert_eq!(stale["error"]["code"], "CURSOR_EXPIRED");
    let made = execute(
        &files,
        "filesystem.mkdir",
        json!({"path":"nested/sub","parents":true}),
    )
    .await;
    assert_eq!(made["state"], "SUCCEEDED");
    std::fs::write(root.path().join("workspace/nested/sub/file"), b"fixture").unwrap();
    let stat = execute(&files, "filesystem.stat", json!({"path":"nested"})).await;
    let deleted = execute(
        &files,
        "filesystem.delete",
        json!({"path":"nested","recursive":true,"expected_revision":stat["result"]["revision"]}),
    )
    .await;
    assert_eq!(deleted["state"], "SUCCEEDED");
    assert!(!root.path().join("workspace/nested").exists());
}
#[tokio::test]
async fn canceled_write_never_publishes_and_large_binary_output_is_spooled() {
    let root = tempfile::tempdir().unwrap();
    let files = provider(root.path());
    let cancel = CancellationToken::new();
    cancel.cancel();
    let result=files.execute(json!({"operation":"filesystem.write","payload":validate_operation("filesystem.write",json!({"path":"canceled","content":"never"})).unwrap(),"operation_id":"op_cancel","remaining_timeout_ms":10000,"context":{"workspace_id":"default"}}),cancel).await.unwrap();
    assert_eq!(result["state"], "CANCELLED");
    assert!(!root.path().join("workspace/canceled").exists());
    let bytes = vec![255u8; 2 * 1024 * 1024];
    std::fs::write(root.path().join("workspace/binary"), &bytes).unwrap();
    let read = execute(
        &files,
        "filesystem.read",
        json!({"path":"binary","binary":true}),
    )
    .await;
    assert_eq!(read["state"], "SUCCEEDED");
    let path = read["result"]["spool_path"].as_str().unwrap();
    assert_eq!(
        racp_contract::digest(std::fs::read(path).unwrap()),
        racp_contract::digest(bytes)
    );
}
