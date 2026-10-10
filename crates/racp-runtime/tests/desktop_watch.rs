use racp_runtime::providers::desktop::{HeldInputs, InterruptionCounter, ReleaseInput};
use std::time::{Duration, Instant};
#[test]
fn foreign_events_increment_only_interruption_counts_and_owned_events_have_no_history() {
    let mut counter = InterruptionCounter::new(123);
    counter.input(0x10, 123, 0x10);
    assert_eq!(counter.foreign(), 0);
    counter.input(0x10, 999, 0x10);
    counter.input(0, 123, 0x10);
    assert_eq!(counter.foreign(), 2);
    let first = counter.window_generation(1);
    counter.window_event(0x8001, 1, 0, 0);
    assert_ne!(first, counter.window_generation(1));
    counter.window_event(3, 1, 0, 0);
    counter.window_event(3, 1, 0, 0);
    assert_eq!(counter.foreground(), 2);
}
#[test]
fn guardian_retains_only_owned_downs_and_preserves_unicode_and_mouse_release_units() {
    let now = Instant::now();
    let mut held = HeldInputs::default();
    held.keyboard(0xe7, 0xd55c, 0x10, now);
    held.mouse(0x201, now);
    assert_eq!(
        held.snapshot(),
        vec![
            ReleaseInput::Key {
                code: 0,
                scan: 0xd55c,
                flags: 6
            },
            ReleaseInput::Mouse { flags: 4 }
        ]
    );
    held.keyboard(0xe7, 0xd55c, 0x90, now);
    assert_eq!(held.snapshot(), vec![ReleaseInput::Mouse { flags: 4 }]);
    assert!(held.overdue(now + Duration::from_secs(2), Duration::from_secs(1)));
    held.mouse(0x202, now);
    assert!(held.snapshot().is_empty());
    assert!(!held.overdue(now + Duration::from_secs(2), Duration::from_secs(1)));
}
#[test]
fn too_many_owned_downs_fail_closed_instead_of_losing_release_authority() {
    let now = Instant::now();
    let mut held = HeldInputs::default();
    for vk in 1..=17 {
        held.keyboard(vk, 0, 0x10, now);
    }
    assert_eq!(held.snapshot().len(), 16);
    assert!(held.overdue(now, Duration::from_secs(1)));
}
