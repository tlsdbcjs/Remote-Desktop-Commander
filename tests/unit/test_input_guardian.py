import time
from dataclasses import replace
from pathlib import Path

import pytest
from racp_agent.broker.guard_config import Controller, GuardConfig
from racp_agent.broker.guard_ledger import HeldInputs
from racp_agent.broker.identity import Identity


def test_owned_unicode_extended_modifier_and_mouse_release_state_is_bounded() -> None:
    ledger = HeldInputs()
    ledger.keyboard(0xE7, 0xD83D, 0x10)
    ledger.keyboard(0xA3, 29, 0x11)
    ledger.mouse(0x201, 0)
    assert {(r.kind, r.code, r.scan, r.flags) for r in ledger.snapshot()} == {
        ("key", 0, 0xD83D, 6),
        ("key", 0xA3, 0, 3),
        ("mouse", 0, 0, 4),
    }
    ledger.keyboard(0xE7, 0xD83D, 0x90)
    ledger.keyboard(0xA3, 29, 0x91)
    ledger.mouse(0x202, 0)
    assert not ledger.snapshot()
    for code in range(30):
        ledger.keyboard(code + 1, code, 0x10)
    assert len(ledger.pending) == 16 and ledger.failure


def test_repeat_down_does_not_extend_hard_hold_deadline() -> None:
    ledger = HeldInputs()
    ledger.keyboard(0xA2, 29, 0x10)
    key, release = next(iter(ledger.pending.items()))
    ledger.pending[key] = replace(release, since=time.monotonic() - 10)
    ledger.keyboard(0xA2, 29, 0x10)
    assert ledger.overdue(3)
    ledger.keyboard(0xA2, 29, 0x90)
    assert not ledger.overdue(3)


def test_guardian_parent_controller_pid_birth_session_sid_and_server_binding() -> None:
    parent = Identity(100, 1.0, "S-1-5-21-1-2-3-1001", 1, 8192)
    actor = Identity(200, 2.0, "S-1-5-21-1-2-3-1002", 0, 8192, ("S-1-5-80-1-2-3-4-5",))
    controller = Controller(
        pid=actor.pid,
        created=actor.created,
        sid=actor.sid,
        session=0,
        service_sid=actor.service_sids[0],
    )
    cfg = GuardConfig.pair_guard(parent, actor.service_sids[0], controller)
    cfg.require_agent(parent)
    assert cfg.parent_caller()
    cfg.require_agent(actor)
    assert not cfg.parent_caller()
    for wrong in (
        replace(parent, pid=101),
        replace(parent, created=2),
        replace(parent, session=2),
        replace(actor, service_sids=()),
        replace(actor, pid=201),
        replace(actor, sid=parent.sid),
    ):
        with pytest.raises(PermissionError):
            cfg.require_agent(wrong)
    guardian = replace(parent, pid=300, created=3)
    cfg = cfg.model_copy(
        update={"guardian_pid": guardian.pid, "guardian_created": guardian.created}
    )
    cfg.require_broker(guardian)
    with pytest.raises(PermissionError):
        cfg.require_broker(replace(guardian, created=4))


def test_guardian_cleanup_checks_owned_file_before_any_scheduler_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from racp_agent.broker.guard_task import cleanup_guard_config

    parent = Identity(100, 1, "S-1-5-21-1-2-3-1001", 1, 8192)
    root = tmp_path / "racp-input-guard-owned"
    root.mkdir()
    config = GuardConfig.pair_guard(parent, parent.sid).model_copy(
        update={"launch_backend": "task", "cleanup_directory": str(root)}
    )
    file = root / (config.pair_id + ".json")
    file.write_text(config.model_dump_json(), encoding="utf-8")
    removed = []
    monkeypatch.setattr(
        "racp_agent.broker.guard_task.remove_task", lambda name: removed.append(name)
    )
    wrong = config.model_copy(update={"secret": "x" * 43})
    with pytest.raises(PermissionError):
        cleanup_guard_config(wrong)
    assert file.exists() and not removed
    cleanup_guard_config(config)
    assert not root.exists() and removed == ["RACP-InputGuard-" + config.pair_id]
    cleanup_guard_config(config)  # Parent and guardian may both finish the same cleanup.
    with pytest.raises(PermissionError):
        cleanup_guard_config(config.model_copy(update={"cleanup_directory": str(tmp_path)}))
