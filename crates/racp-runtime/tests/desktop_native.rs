#![cfg(windows)]
use racp_runtime::providers::desktop::native_identity::PinnedPeer;
#[test]
fn native_peer_identity_is_pinned_and_includes_os_sid_session_integrity_and_birth() {
    let first = PinnedPeer::open(std::process::id()).unwrap();
    let second = PinnedPeer::open(std::process::id()).unwrap();
    assert_eq!(first.identity(), second.identity());
    assert_eq!(first.identity().pid, std::process::id());
    assert!(first.identity().created > 0.0);
    assert!(first.identity().sid.starts_with("S-1-"));
    assert!(first.identity().integrity >= 4096);
    first.alive().unwrap();
    assert!(PinnedPeer::open(u32::MAX).is_err());
}
