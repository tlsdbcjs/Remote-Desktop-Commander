use racp_core::{inspect_connection, SecretStore};
use serde_json::json;
use std::{collections::BTreeMap, fs};
use tempfile::tempdir;
#[test]
fn connection_changed_or_expired() {
    let root = tempdir().unwrap();
    let path = root.path().join("connect.racp");
    fs::write(&path,json!({"version":1,"gateway":"https://gateway.example","token":"private_one_use_token_12345","expires_at":"2030-01-01T00:00:00Z","ca_pem":null}).to_string()).unwrap();
    let preview = inspect_connection(&path, None).unwrap();
    assert!(preview.get("token").is_none());
    assert_eq!(
        inspect_connection(&path, Some(&"0".repeat(64)))
            .unwrap_err()
            .code
            .0,
        "CONNECTION_FILE_CHANGED"
    );
    fs::write(&path,json!({"gateway":"https://gateway.example","token":"private_one_use_token_12345","expires_at":"2000-01-01T00:00:00Z"}).to_string()).unwrap();
    assert_eq!(
        inspect_connection(&path, None).unwrap_err().code.0,
        "CONNECTION_FILE_EXPIRED"
    );
}
#[test]
#[cfg(unix)]
fn credentials_private_and_bounded() {
    use std::os::unix::fs::PermissionsExt;
    let root = tempdir().unwrap();
    let path = root.path().join("credential.bin");
    fs::write(&path, "{}").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
    let store = SecretStore::new(path.clone());
    assert!(store.load().is_err());
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(store.load().unwrap(), BTreeMap::new());
    fs::write(&path, vec![b' '; 65537]).unwrap();
    assert!(store.load().is_err());
}
#[test]
fn connection_size_and_origin_are_bounded() {
    let root = tempdir().unwrap();
    let path = root.path().join("connect.racp");
    fs::write(&path,json!({"gateway":format!("https://{}.example","x".repeat(2050)),"token":"private_one_use_token_12345","expires_at":"2030-01-01T00:00:00Z"}).to_string()).unwrap();
    assert_eq!(
        inspect_connection(&path, None).unwrap_err().code.0,
        "CONNECTION_FILE_INVALID"
    );
}
