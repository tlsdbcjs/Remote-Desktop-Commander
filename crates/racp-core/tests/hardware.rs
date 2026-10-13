use racp_contract::hardware::*;
use racp_core::hardware::*;
use std::time::{Duration, Instant};
fn observed(device: &str, interface: &str) -> ObservedInterface {
    ObservedInterface {
        device_key: device.into(),
        interface_key: interface.into(),
        vendor_id: Some(0x1234),
        product_id: Some(0x5678),
    }
}
fn caller() -> HardwareCaller {
    HardwareCaller {
        agent_device_id: "dev_pc".into(),
        agent_boot_id: "boot_one".into(),
        principal_id: "owner_one".into(),
        workspace_id: "default".into(),
    }
}
fn setup() -> (
    HardwareInventory,
    HardwareSessions,
    HardwareCaller,
    HardwareLease,
    Instant,
) {
    let now = Instant::now();
    let mut inventory = HardwareInventory::new("dev_pc", "boot_one").unwrap();
    let target = inventory
        .refresh(vec![observed("fixture-device", "fixture-interface")])
        .unwrap()
        .remove(0)
        .target;
    let mut sessions = HardwareSessions::new("dev_pc", "boot_one").unwrap();
    let caller = caller();
    let lease = sessions
        .reserve(caller.clone(), target, &inventory, 1000, now)
        .unwrap();
    (inventory, sessions, caller, lease, now)
}
fn read_plan() -> HardwareReadPlan {
    HardwareReadPlan {
        kind: CollectionKind::DescriptorSnapshot,
        protocol_id: None,
        method_revision: "fixture_v1".into(),
    }
}
fn request(budget: HardwareIoBudget) -> HardwareIoRequest {
    HardwareIoRequest {
        operation_id: racp_contract::new_id("op"),
        budget,
        plan: read_plan(),
    }
}
fn budget() -> HardwareIoBudget {
    HardwareIoBudget {
        timeout_ms: 100,
        max_bytes: 1024,
    }
}
#[test]
fn composite_interfaces_share_device_but_duplicate_vid_pid_never_identify_another_device() {
    let mut inventory = HardwareInventory::new("dev_pc", "boot_one").unwrap();
    let rows = inventory
        .refresh(vec![
            observed("first", "interface-a"),
            observed("first", "interface-b"),
            observed("second", "interface-c"),
        ])
        .unwrap();
    assert_eq!(rows[0].target.hardware_id, rows[1].target.hardware_id);
    assert_ne!(rows[0].target.interface_id, rows[1].target.interface_id);
    assert_ne!(rows[0].target.hardware_id, rows[2].target.hardware_id);
    assert_eq!(
        rows,
        inventory
            .refresh(vec![
                observed("first", "interface-a"),
                observed("first", "interface-b"),
                observed("second", "interface-c")
            ])
            .unwrap()
    );
}
#[test]
fn invalid_snapshot_never_partially_replaces_inventory() {
    let (mut inventory, _, _, lease, _) = setup();
    assert!(inventory
        .refresh(vec![observed("new", "a"), observed("new", "a")])
        .is_err());
    assert!(inventory.is_current(&lease.target));
    assert!(inventory.refresh(vec![observed("\0", "a")]).is_err());
    assert!(inventory.is_current(&lease.target));
    assert!(inventory
        .refresh(
            (0..=MAX_HARDWARE_INTERFACES)
                .map(|i| observed(&format!("d{i}"), "a"))
                .collect()
        )
        .is_err());
    assert!(inventory.is_current(&lease.target));
}
#[test]
fn unplug_replug_and_lost_continuity_reject_old_connection_generations() {
    let (mut inventory, mut sessions, caller, lease, now) = setup();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    inventory.refresh(vec![]).unwrap();
    let replacement = inventory
        .refresh(vec![observed("fixture-device", "fixture-interface")])
        .unwrap()
        .remove(0)
        .target;
    assert_ne!(
        replacement.connection_generation,
        lease.target.connection_generation
    );
    assert_eq!(
        sessions
            .begin_io(&caller, &lease, &inventory, request(budget()), now)
            .unwrap_err()
            .code
            .0,
        "HARDWARE_CONNECTION_STALE"
    );
    inventory.invalidate();
    assert!(!inventory.is_current(&replacement));
}
#[test]
fn disconnected_io_completion_cannot_create_publishable_collection_receipt() {
    let (mut inventory, mut sessions, caller, lease, now) = setup();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    let io = sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .unwrap();
    inventory.invalidate();
    assert_eq!(
        sessions
            .finish_io(&caller, &lease, &inventory, &io, 3, now)
            .unwrap_err()
            .code
            .0,
        "HARDWARE_CONNECTION_STALE"
    );
    assert_eq!(
        sessions.phase(&caller, &lease).unwrap(),
        HardwarePhase::Unknown
    );
    assert!(!sessions.cleanup_confirmed());
}
#[test]
fn changed_device_metadata_rotates_all_composite_interfaces() {
    let mut inventory = HardwareInventory::new("dev_pc", "boot_one").unwrap();
    let old = inventory
        .refresh(vec![observed("d", "a"), observed("d", "b")])
        .unwrap();
    let mut replacement = observed("d", "a");
    replacement.product_id = Some(0x9999);
    let new = inventory
        .refresh(vec![replacement, observed("d", "b")])
        .unwrap();
    assert!(old.iter().all(|row| !inventory.is_current(&row.target)));
    assert_eq!(
        new[0].target.connection_generation,
        new[1].target.connection_generation
    );
}
#[test]
fn caller_pc_boot_workspace_and_exact_lease_are_fenced_without_changing_owner_state() {
    let (inventory, mut sessions, caller, lease, now) = setup();
    for field in 0..4 {
        let mut foreign = caller.clone();
        match field {
            0 => foreign.agent_device_id = "dev_other".into(),
            1 => foreign.agent_boot_id = "boot_other".into(),
            2 => foreign.principal_id = "owner_other".into(),
            _ => foreign.workspace_id = "other".into(),
        }
        assert!(sessions
            .activate(&foreign, &lease, &inventory, now)
            .is_err());
        assert!(sessions.begin_close(&foreign, &lease).is_err());
    }
    let mut stale = lease.clone();
    stale.lease_id = racp_contract::new_id("hwlease");
    assert!(sessions.activate(&caller, &stale, &inventory, now).is_err());
    assert_eq!(
        sessions.phase(&caller, &lease).unwrap(),
        HardwarePhase::Reserved
    );
    assert!(sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .is_err());
    let mut another = HardwareSessions::new("dev_pc", "boot_other").unwrap();
    assert!(another.phase(&caller, &lease).is_err());
}
#[test]
fn lease_expiry_and_io_deadline_never_publish_late_completion_as_success() {
    let (inventory, mut sessions, caller, lease, now) = setup();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    let io = sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .unwrap();
    assert_eq!(
        sessions
            .finish_io(
                &caller,
                &lease,
                &inventory,
                &io,
                10,
                now + Duration::from_millis(100)
            )
            .unwrap_err()
            .code
            .0,
        "HARDWARE_IO_OUTCOME_UNKNOWN"
    );
    assert_eq!(
        sessions.phase(&caller, &lease).unwrap(),
        HardwarePhase::Unknown
    );
    sessions.begin_close(&caller, &lease).unwrap();
    sessions.complete_close(&caller, &lease, true).unwrap();
    let lease = sessions
        .reserve(caller.clone(), lease.target.clone(), &inventory, 1000, now)
        .unwrap();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    assert_eq!(
        sessions
            .begin_io(
                &caller,
                &lease,
                &inventory,
                request(budget()),
                now + Duration::from_millis(1000)
            )
            .unwrap_err()
            .code
            .0,
        "HARDWARE_LEASE_EXPIRED"
    );
    assert!(!sessions.cleanup_confirmed());
}
#[test]
fn stop_and_close_require_pending_io_drain_and_confirmed_native_close() {
    let (inventory, mut sessions, caller, lease, now) = setup();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    let io = sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .unwrap();
    sessions.quiesce();
    assert_eq!(
        sessions
            .reserve(caller.clone(), lease.target.clone(), &inventory, 1000, now)
            .unwrap_err()
            .code
            .0,
        "HARDWARE_STOPPING"
    );
    assert!(sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .is_err());
    assert_eq!(
        sessions
            .complete_close(&caller, &lease, true)
            .unwrap_err()
            .code
            .0,
        "HARDWARE_CLEANUP_UNCONFIRMED"
    );
    assert_eq!(
        sessions
            .finish_io(&caller, &lease, &inventory, &io, 0, now)
            .unwrap_err()
            .code
            .0,
        "CANCELLED"
    );
    assert!(sessions.complete_close(&caller, &lease, false).is_err());
    assert!(!sessions.cleanup_confirmed());
    sessions.complete_close(&caller, &lease, true).unwrap();
    sessions.complete_close(&caller, &lease, true).unwrap();
    assert!(sessions.cleanup_confirmed());
    assert!(sessions
        .finish_io(&caller, &lease, &inventory, &io, 0, now)
        .is_err());
}
#[test]
fn session_quotas_pending_io_byte_limits_and_transfer_overflow_fail_closed() {
    let (inventory, mut sessions, caller, lease, now) = setup();
    for _ in 0..3 {
        sessions
            .reserve(caller.clone(), lease.target.clone(), &inventory, 1000, now)
            .unwrap();
    }
    assert!(sessions
        .reserve(caller.clone(), lease.target.clone(), &inventory, 1000, now)
        .is_err());
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    let huge = HardwareIoBudget {
        timeout_ms: 100,
        max_bytes: MAX_HARDWARE_IO_BYTES,
    };
    for _ in 0..4 {
        sessions
            .begin_io(&caller, &lease, &inventory, request(huge.clone()), now)
            .unwrap();
    }
    assert!(sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .is_err());
    let (_, mut sessions, caller, lease, now) = setup();
    sessions
        .activate(&caller, &lease, &inventory, now)
        .unwrap_err(); // Different inventory cannot adopt a target.
    let (inventory, mut sessions, caller, lease, now) = setup();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    let io = sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .unwrap();
    assert!(sessions
        .finish_io(&caller, &lease, &inventory, &io, 1025, now)
        .is_err());
    assert_eq!(
        sessions.phase(&caller, &lease).unwrap(),
        HardwarePhase::Unknown
    );
    assert!(!sessions.cleanup_confirmed());
}
#[test]
fn uncertain_session_never_produces_a_receipt_from_another_parallel_io() {
    let (inventory, mut sessions, caller, lease, now) = setup();
    sessions.activate(&caller, &lease, &inventory, now).unwrap();
    let bad = sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .unwrap();
    let other = sessions
        .begin_io(&caller, &lease, &inventory, request(budget()), now)
        .unwrap();
    assert!(sessions
        .finish_io(&caller, &lease, &inventory, &bad, 1025, now)
        .is_err());
    assert!(sessions
        .finish_io(&caller, &lease, &inventory, &other, 1, now)
        .is_err());
    assert_eq!(
        sessions.phase(&caller, &lease).unwrap(),
        HardwarePhase::Unknown
    );
    assert!(!sessions.cleanup_confirmed());
}
