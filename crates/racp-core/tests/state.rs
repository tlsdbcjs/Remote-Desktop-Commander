use racp_contract::digest;
use racp_core::{editable_settings, information, inspect_connection, update_settings, SecretStore};
use serde_json::json;
use std::{collections::BTreeMap, fs};
use tempfile::tempdir;
fn credentials(root: &std::path::Path) -> BTreeMap<String, String> {
    let workspace = root.join("한글 공백 workspace");
    fs::create_dir(&workspace).unwrap();
    BTreeMap::from([("gateway".into(),"https://gateway.example".into()),("device_id".into(),"dev_existing".into()),("credential".into(),"private_device_token_1234567890".into()),("agent_settings".into(),json!({"version":1,"gateway":"https://gateway.example","device_id":"dev_existing","workspace":workspace,"allowed_workspaces":[],"data_dir":root.join("data"),"profile":"read_only","ca_file":null,"desktop_enabled":false}).to_string())])
}
#[test]
fn old_credentials_roundtrip() {
    let root = tempdir().unwrap();
    let value = credentials(root.path());
    let store = SecretStore::new(root.path().join("credential.bin"));
    store.save(&value, false).unwrap();
    assert_eq!(store.load().unwrap(), value);
    let info = information(root.path()).unwrap();
    assert_eq!(info["device_id"], "dev_existing");
    assert!(info.get("credential").is_none());
    assert!(store.save(&value, false).is_err());
    assert_eq!(store.load().unwrap(), value);
}
#[test]
fn settings_revision_conflict_preserves_identity() {
    let root = tempdir().unwrap();
    let value = credentials(root.path());
    let path = root.path().join("credential.bin");
    SecretStore::new(path.clone()).save(&value, false).unwrap();
    let settings = editable_settings(root.path()).unwrap();
    assert_eq!(settings["revision"], digest(fs::read(&path).unwrap()));
    let mut update = json!({"action":"update_settings","revision":"0".repeat(64),"gateway":"https://new.example","workspace":root.path(),"ca_file":null,"profile":"standard","allowed_workspaces":[]});
    assert_eq!(
        update_settings(root.path(), update.clone())
            .unwrap_err()
            .code
            .0,
        "SETTINGS_CHANGED"
    );
    assert_eq!(SecretStore::new(path.clone()).load().unwrap(), value);
    update["revision"] = settings["revision"].clone();
    let info = update_settings(root.path(), update).unwrap();
    assert_eq!(info["gateway"], "https://new.example");
    assert_eq!(info["device_id"], "dev_existing");
    assert_eq!(
        SecretStore::new(path).load().unwrap()["credential"],
        value["credential"]
    );
    assert_eq!(
        fs::read_dir(root.path().join("settings-backups"))
            .unwrap()
            .count(),
        1
    );
}
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
#[cfg(unix)]
fn dangling_credential_and_write_link_are_rejected() {
    let root = tempdir().unwrap();
    let target = root.path().join("original");
    fs::write(&target, "preserved").unwrap();
    let path = root.path().join("credential.bin");
    std::os::unix::fs::symlink(&target, &path).unwrap();
    assert!(SecretStore::new(path.clone())
        .save(&BTreeMap::new(), true)
        .is_err());
    assert_eq!(fs::read_to_string(&target).unwrap(), "preserved");
    fs::remove_file(&path).unwrap();
    std::os::unix::fs::symlink(root.path().join("missing"), &path).unwrap();
    assert!(information(root.path()).is_err());
}
#[test]
fn settings_busy_does_not_overwrite() {
    let root = tempdir().unwrap();
    let value = credentials(root.path());
    let path = root.path().join("credential.bin");
    SecretStore::new(path.clone()).save(&value, false).unwrap();
    let settings = editable_settings(root.path()).unwrap();
    let _agent = racp_core::InstanceLock::acquire(
        &root
            .path()
            .join(format!("agent-{}.lock", digest("dev_existing"))),
    )
    .unwrap();
    let request = json!({"action":"update_settings","revision":settings["revision"],"gateway":"https://new.example","workspace":root.path(),"profile":"read_only","ca_file":null,"allowed_workspaces":[]});
    assert_eq!(
        update_settings(root.path(), request).unwrap_err().code.0,
        "SETTINGS_BUSY"
    );
    assert_eq!(SecretStore::new(path).load().unwrap(), value);
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
