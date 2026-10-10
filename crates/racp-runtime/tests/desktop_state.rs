use racp_runtime::providers::desktop::{DesktopState, Scope, Snapshot};
use serde_json::json;
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};
fn scope(owner: &str) -> Scope {
    Scope {
        owner: owner.into(),
        device: "dev_one".into(),
    }
}
fn snapshot() -> Snapshot {
    Snapshot {
        available: true,
        unavailable_code: "SESSION_UNAVAILABLE",
        foreground: Some("win_one".into()),
        input_tick: 7,
        foreground_tick: 11,
        layout_revision: "layout_one".into(),
        windows: BTreeMap::from([(
            "win_one".into(),
            json!({"pid":123,"create_time":99.0,"bounds":[0,0,100,100],"generation":1}),
        )]),
    }
}
fn input(lease: &str, observation: &str) -> serde_json::Value {
    json!({"session_id":1,"lease_id":lease,"observation_id":observation,"layout_revision":"layout_one","expected_window_id":"win_one"})
}
#[test]
fn desktop_input_is_scoped_and_a_fresh_observation_is_required_after_dispatch() {
    let now = Instant::now();
    let mut state = DesktopState::new(1);
    let env = snapshot();
    let lease = state
        .acquire(&scope("owner_one"), 15000, &env, now)
        .unwrap();
    let observation = state.observe(&scope("owner_one"), &env, now).unwrap();
    let payload = input(&lease, &observation);
    assert_eq!(
        state
            .guard(&scope("owner_two"), &payload, &env, now, false)
            .unwrap_err()
            .code
            .0,
        "PERMISSION_DENIED"
    );
    state
        .guard(&scope("owner_one"), &payload, &env, now, false)
        .unwrap();
    state.dispatched(&env, false).unwrap();
    assert_eq!(
        state
            .guard(&scope("owner_one"), &payload, &env, now, false)
            .unwrap_err()
            .code
            .0,
        "STALE_OBSERVATION"
    );
    assert!(state.needs_release());
}
#[test]
fn physical_input_and_foreground_change_away_and_back_revoke_the_lease() {
    let now = Instant::now();
    let mut state = DesktopState::new(1);
    let mut env = snapshot();
    let lease = state
        .acquire(&scope("owner_one"), 15000, &env, now)
        .unwrap();
    let observation = state.observe(&scope("owner_one"), &env, now).unwrap();
    env.input_tick += 1;
    assert_eq!(
        state
            .guard(
                &scope("owner_one"),
                &input(&lease, &observation),
                &env,
                now,
                false
            )
            .unwrap_err()
            .code
            .0,
        "STALE_OBSERVATION"
    );
    assert!(state.needs_release());
    assert_eq!(
        state
            .acquire(&scope("owner_two"), 15000, &env, now)
            .unwrap_err()
            .code
            .0,
        "RESOURCE_BUSY"
    );
    state.cleanup_confirmed();
    let lease = state
        .acquire(&scope("owner_two"), 15000, &env, now)
        .unwrap();
    let observation = state.observe(&scope("owner_two"), &env, now).unwrap();
    env.foreground_tick += 2;
    assert_eq!(
        state
            .guard(
                &scope("owner_two"),
                &input(&lease, &observation),
                &env,
                now,
                false
            )
            .unwrap_err()
            .code
            .0,
        "STALE_OBSERVATION"
    );
}
#[test]
fn expiry_window_reuse_layout_change_and_unavailable_desktop_fail_closed() {
    let now = Instant::now();
    let mut state = DesktopState::new(1);
    let env = snapshot();
    let lease = state.acquire(&scope("owner_one"), 1000, &env, now).unwrap();
    let observation = state.observe(&scope("owner_one"), &env, now).unwrap();
    assert_eq!(
        state
            .guard(
                &scope("owner_one"),
                &input(&lease, &observation),
                &env,
                now + Duration::from_secs(2),
                false
            )
            .unwrap_err()
            .code
            .0,
        "LEASE_EXPIRED"
    );
    state.cleanup_confirmed();
    let lease = state
        .acquire(&scope("owner_one"), 15000, &env, now)
        .unwrap();
    let observation = state.observe(&scope("owner_one"), &env, now).unwrap();
    let mut changed = env.clone();
    changed.windows.get_mut("win_one").unwrap()["generation"] = json!(2);
    assert_eq!(
        state
            .guard(
                &scope("owner_one"),
                &input(&lease, &observation),
                &changed,
                now,
                false
            )
            .unwrap_err()
            .code
            .0,
        "STALE_OBSERVATION"
    );
    state.cleanup_confirmed();
    let mut unavailable = env;
    unavailable.available = false;
    unavailable.unavailable_code = "SECURE_DESKTOP";
    assert_eq!(
        state
            .acquire(&scope("owner_one"), 1000, &unavailable, now)
            .unwrap_err()
            .code
            .0,
        "SECURE_DESKTOP"
    );
}
