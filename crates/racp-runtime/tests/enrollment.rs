use racp_runtime::enroll;
use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
async fn server(body: &str, status: u16) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let body = body.to_string();
    let task = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut buf = vec![0; 16384];
        let _ = socket.read(&mut buf).await.unwrap();
        let response=format!("HTTP/1.1 {status} OK\r\nContent-Length: {}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{body}",body.len());
        socket.write_all(response.as_bytes()).await.unwrap();
    });
    (url, task)
}
fn request(url: &str, workspace: &std::path::Path) -> serde_json::Value {
    json!({"action":"enroll","gateway":url,"workspace":workspace,"token":"private_one_use_token_123456","profile":"read_only"})
}
#[tokio::test]
async fn enrollment_success_and_secret_is_local() {
    let root = tempfile::tempdir().unwrap();
    let (url, task) = server(
        r#"{"device_id":"dev_enrolled","credential":"private_device_credential_12345"}"#,
        200,
    )
    .await;
    let info = enroll(request(&url, root.path()), &root.path().join("state"))
        .await
        .unwrap();
    task.await.unwrap();
    assert_eq!(info["device_id"], "dev_enrolled");
    assert!(info.get("credential").is_none());
    assert!(info.get("token").is_none());
    let saved = racp_core::SecretStore::new(root.path().join("state/credential.bin"))
        .load()
        .unwrap();
    assert_eq!(saved["credential"], "private_device_credential_12345");
    assert!(!root.path().join("state/.enrollment-in-progress").exists());
}
#[tokio::test]
async fn enrollment_preflight() {
    let root = tempfile::tempdir().unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let state = root.path().join("state");
    let error = enroll(request(&url, &root.path().join("missing")), &state)
        .await
        .unwrap_err();
    assert_eq!(error.code.0, "WORKSPACE_INVALID");
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(30), listener.accept())
            .await
            .is_err()
    );
    let mut ca = request(&url, root.path());
    ca["ca_file"] = json!(root.path().join("missing.pem"));
    assert_eq!(enroll(ca, &state).await.unwrap_err().code.0, "CA_INVALID");
    let mut plain = request("http://example.org", root.path());
    assert_eq!(
        enroll(plain.clone(), &state).await.unwrap_err().code.0,
        "GATEWAY_INVALID"
    );
    plain["gateway"] = json!(url);
    plain["token"] = json!("has whitespace in private_token");
    assert_eq!(
        enroll(plain, &state).await.unwrap_err().code.0,
        "TOKEN_INVALID"
    );
}
#[tokio::test]
async fn enrollment_response_bounds() {
    let root = tempfile::tempdir().unwrap();
    let body = " ".repeat(4097);
    let (url, task) = server(&body, 200).await;
    let err = enroll(request(&url, root.path()), &root.path().join("state"))
        .await
        .unwrap_err();
    task.await.unwrap();
    assert!(!format!("{err:?}").contains("private_one_use"));
    assert!(!root.path().join("state/credential.bin").exists());
}
#[tokio::test]
async fn enrollment_rejection_keeps_registration_empty() {
    let root = tempfile::tempdir().unwrap();
    let (url, task) = server("{}", 403).await;
    let err = enroll(request(&url, root.path()), &root.path().join("state"))
        .await
        .unwrap_err();
    task.await.unwrap();
    assert_eq!(err.code.0, "TOKEN_REJECTED");
    assert!(!root.path().join("state/credential.bin").exists());
}
