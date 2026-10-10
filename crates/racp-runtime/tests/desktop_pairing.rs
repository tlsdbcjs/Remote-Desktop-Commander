use racp_runtime::providers::desktop::{PairConfig, PeerIdentity};
use serde_json::json;
use std::collections::BTreeSet;
fn pair() -> PairConfig {
    PairConfig::decode(&serde_json::to_vec(&json!({"version":1,"pair_id":"0123456789abcdef0123456789abcdef","session_id":2,"user_sid":"S-1-5-21-1-2-3-1001","agent_sid":"S-1-5-21-1-2-3-1001","agent_service_sid":null,"agent_pid":123,"agent_created":1700000000.25,"agent_session":2,"secret":"a".repeat(43),"job_name":null})).unwrap()).unwrap()
}
fn peer() -> PeerIdentity {
    PeerIdentity {
        pid: 123,
        created: 1700000000.25,
        sid: "S-1-5-21-1-2-3-1001".into(),
        session: 2,
        integrity: 8192,
        service_sids: BTreeSet::new(),
        administrator: false,
    }
}
#[test]
fn pairing_uses_os_peer_pid_birth_sid_session_and_enabled_service_identity() {
    let mut config = pair();
    let original = peer();
    config.require_agent(&original).unwrap();
    for modified in [
        PeerIdentity {
            pid: 124,
            ..original.clone()
        },
        PeerIdentity {
            created: 1700000001.25,
            ..original.clone()
        },
        PeerIdentity {
            session: 3,
            ..original.clone()
        },
        PeerIdentity {
            sid: "S-1-5-21-1-2-3-1002".into(),
            ..original.clone()
        },
    ] {
        assert_eq!(
            config.require_agent(&modified).unwrap_err().code.0,
            "PERMISSION_DENIED"
        );
    }
    config.agent_service_sid = Some("S-1-5-80-1-2-3-4-5".into());
    assert!(config.require_agent(&original).is_err());
    let mut service = original;
    service
        .service_sids
        .insert(config.agent_service_sid.clone().unwrap());
    config.require_agent(&service).unwrap();
}
#[test]
fn pairing_frames_are_bounded_strict_and_sid_strings_cannot_inject_an_acl() {
    let config = pair();
    let mut raw = serde_json::to_value(&config).unwrap();
    raw["user_sid"] = json!("S-1-5-21-1)(A;;GA;;;WD)");
    assert!(PairConfig::decode(&serde_json::to_vec(&raw).unwrap()).is_err());
    raw = serde_json::to_value(&config).unwrap();
    raw["unexpected"] = json!(true);
    assert!(PairConfig::decode(&serde_json::to_vec(&raw).unwrap()).is_err());
    assert!(PairConfig::decode(&vec![b' '; 4097]).is_err());
}
#[test]
fn pairing_challenges_are_role_and_nonce_bound_and_match_the_legacy_python_encoding() {
    let config = pair();
    let server = "b".repeat(64);
    let client = "c".repeat(64);
    let proof = config.proof("broker", &server, &client).unwrap();
    assert_eq!(
        proof,
        "0dd96999a5bec29fea7d12389e636062b70676d7f958608e98fb9c300c96abf1"
    );
    config.verify("broker", &server, &client, &proof).unwrap();
    assert!(config.verify("agent", &server, &client, &proof).is_err());
    assert!(config.verify("broker", &client, &server, &proof).is_err());
}
