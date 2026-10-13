use racp_contract::service_scope::*;
use serde_json::json;

#[test]
fn service_roles_are_bound_to_exact_sid_and_interactive_session() {
    let scope: ServiceScope = serde_json::from_value(json!({
        "service_name": "RACPNativeAgent", "service_sid": "S-1-5-80-1-2-3-4-5",
        "controllers": [
            {"user_sid":"S-1-5-21-1-2-3-1001","session_ids":[1],"role":"observer"},
            {"user_sid":"S-1-5-21-1-2-3-1002","session_ids":[2],"role":"controller"}
        ]
    }))
    .unwrap();
    scope.validate().unwrap();
    assert_eq!(
        scope.role("S-1-5-21-1-2-3-1001", 1),
        Some(ControllerRole::Observer)
    );
    assert_eq!(
        scope.role("S-1-5-21-1-2-3-1002", 2),
        Some(ControllerRole::Controller)
    );
    for (sid, session) in [
        ("S-1-5-21-1-2-3-1001", 2),
        ("S-1-5-21-1-2-3-1002", 0),
        ("S-1-5-18", 2),
        ("S-1-5-21-1-2-3-1003", 2),
    ] {
        assert_eq!(scope.role(sid, session), None);
    }
}
