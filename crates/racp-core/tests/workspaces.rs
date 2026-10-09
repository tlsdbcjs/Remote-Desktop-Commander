use racp_core::{WorkspaceSpec, Workspaces};
#[test]
fn selected_workspace_escape_and_identity_fence() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    let docs = root.path().join("docs");
    std::fs::create_dir(&workspace).unwrap();
    std::fs::create_dir(&docs).unwrap();
    let guards = Workspaces::new(
        &workspace,
        &[WorkspaceSpec {
            id: "docs".into(),
            path: docs.clone(),
        }],
    )
    .unwrap();
    assert!(guards.path("default", "../outside").is_err());
    assert!(guards.path("default", docs.to_str().unwrap()).is_err());
    assert_eq!(
        guards.path("docs", "file.txt").unwrap(),
        docs.join("file.txt")
    );
    assert!(guards.path("unapproved", "file").is_err());
    assert!(guards.parent("default", &workspace).is_err());
    #[cfg(unix)]
    {
        std::fs::rename(&workspace, root.path().join("old")).unwrap();
        std::fs::create_dir(&workspace).unwrap();
        assert!(guards.directory("default", &workspace).is_err());
    }
}
#[test]
fn no_follow_dangling_link_and_pinned_ancestor() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    let guards = Workspaces::new(&workspace, &[]).unwrap();
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(root.path().join("outside"), workspace.join("link")).unwrap();
        assert!(guards
            .directory("default", &workspace.join("link"))
            .is_err());
        let parent = guards.parent("default", &workspace.join("link")).unwrap();
        assert!(parent.open_read("link").is_err());
    }
    #[cfg(windows)]
    {
        let parent = guards.directory("default", &workspace).unwrap();
        assert!(std::fs::rename(&workspace, root.path().join("renamed")).is_err());
        drop(parent);
    }
}
