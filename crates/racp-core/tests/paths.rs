use racp_core::validate_local_path;
#[test]
fn state_guard() {
    let root = tempfile::tempdir().unwrap();
    assert!(validate_local_path(root.path()).is_ok());
    assert!(validate_local_path(std::path::Path::new("relative/path")).is_err());
    assert!(validate_local_path(std::path::Path::new("//server/share")).is_err());
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(root.path(), root.path().join("link")).unwrap();
        assert!(validate_local_path(&root.path().join("link/child")).is_err());
    }
}
