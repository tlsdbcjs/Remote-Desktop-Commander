import time
from typing import Any

import pytest
from racp_agent.broker.core import BrokerContext, BrokerCore
from racp_domain.models import RACPError


class Backend:
    def __init__(self) -> None:
        self.active, self.fg, self.input, self.revision = True, "window_test", 1, "layout_1"
        self.bounds = [0, 0, 800, 600]
        self.releases, self.dispatches = 0, 0
        self.fail = False

    def status(self) -> dict[str, Any]:
        return {"available": self.active, "error_code": "SESSION_LOCKED", "reason": "test lock"}

    def foreground(self) -> str | None:
        return self.fg

    def input_tick(self) -> int:
        return self.input

    def foreground_tick(self) -> int:
        return 0

    def acquire_lease(self) -> None:
        pass

    def release_lease(self) -> None:
        pass

    def layout(self) -> dict[str, Any]:
        return {"layout_revision": self.revision, "session_id": 1}

    def windows(self, limit: int) -> list[dict[str, Any]]:
        return [{"window_id": "window_test", "bounds": self.bounds.copy()}][:limit]

    def validate_window(self, window: dict[str, Any]) -> bool:
        return self.bounds == window["bounds"]

    def release_inputs(self) -> None:
        self.releases += 1

    def action(self, operation: str, payload: dict[str, Any], guard: Any) -> dict[str, Any]:
        guard()
        self.dispatches += 1
        if self.fail:
            raise OSError("injected partial dispatch failure")
        return {"dispatched": True}

    def capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {}

    def inspect(self, payload: dict[str, Any], observation: str) -> dict[str, Any]:
        return {}

    def clear_observations(self) -> None:
        pass

    def attach_guard(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"attached": True}

    def guard_info(self) -> dict[str, Any]:
        return {"guardian": None}


def prepare() -> tuple[BrokerCore, Backend, BrokerContext, dict[str, Any]]:
    backend = Backend()
    core = BrokerCore(1, backend)
    context = BrokerContext(owner_id="owner_test", device_id="device_test", operation_id="op_test")
    lease = core.execute("desktop.lease_acquire", {"session_id": 1}, context)
    observed = core.execute("desktop.windows", {"session_id": 1}, context)
    payload = {
        "session_id": 1,
        "lease_id": lease["lease_id"],
        "observation_id": observed["observation_id"],
        "layout_revision": observed["layout_revision"],
        "expected_window_id": "window_test",
        "text": "한글",
    }
    return core, backend, context, payload


@pytest.mark.parametrize(
    "change",
    [
        "foreground",
        "user_input",
        "layout",
        "bounds",
        "expired_observation",
        "expired_lease",
        "locked",
    ],
)
def test_desktop_changed_observation_aborts_before_dispatch_and_releases(change: str) -> None:
    core, backend, context, payload = prepare()
    if change == "foreground":
        backend.fg = "window_other"
    if change == "user_input":
        backend.input += 1
    if change == "layout":
        backend.revision = "layout_2"
    if change == "bounds":
        backend.bounds[0] += 1
    if change == "expired_observation":
        core.observations[payload["observation_id"]].expires = time.monotonic() - 1
    if change == "expired_lease":
        assert core.lease is not None
        core.lease.expires = time.monotonic() - 1
    if change == "locked":
        backend.active = False
    with pytest.raises(RACPError):
        core.execute("desktop.type", payload, context)
    assert backend.dispatches == 0 and backend.releases > 0 and core.lease is None


def test_desktop_exclusive_scope_fresh_observation_and_exception_cleanup() -> None:
    core, backend, context, payload = prepare()
    with pytest.raises(RACPError) as busy:
        core.execute("desktop.lease_acquire", {"session_id": 1}, context)
    assert busy.value.error.code == "RESOURCE_BUSY"
    other = context.model_copy(update={"owner_id": "owner_other"})
    with pytest.raises(RACPError) as denied:
        core.execute("desktop.type", payload, other)
    assert denied.value.error.code == "PERMISSION_DENIED" and core.lease is not None
    with pytest.raises(RACPError) as denied_device:
        core.execute(
            "desktop.type", payload, context.model_copy(update={"device_id": "device_other"})
        )
    assert denied_device.value.error.code == "PERMISSION_DENIED" and core.lease is not None
    wrong = dict(payload, session_id=2)
    with pytest.raises(RACPError):
        core.execute("desktop.type", wrong, context)
    assert core.execute("desktop.type", payload, context)["verification"] == "os_dispatch_only"
    assert backend.dispatches == 1 and not core.observations
    with pytest.raises(RACPError):
        core.execute("desktop.type", payload, context)
    assert backend.dispatches == 1 and core.lease is None
    core, backend, context, payload = prepare()
    backend.fail = True
    with pytest.raises(OSError):
        core.execute("desktop.type", payload, context)
    assert backend.releases > 0 and core.lease is None
    rejected = core.handle(
        {"operation": "shell.exec", "payload": {}, "context": context.model_dump()}
    )
    assert rejected["state"] == "FAILED" and rejected["error"]["code"] == "CAPABILITY_UNAVAILABLE"


def test_input_during_dispatch_is_not_silently_accepted_as_the_new_baseline() -> None:
    core, backend, context, payload = prepare()

    def interrupted(operation: str, payload: dict[str, Any], guard: Any) -> dict[str, Any]:
        guard()
        backend.dispatches += 1
        backend.input += 1
        return {"dispatched": True}

    backend.action = interrupted  # type: ignore[method-assign]
    with pytest.raises(RACPError) as changed:
        core.execute("desktop.type", payload, context)
    assert changed.value.error.code == "STALE_OBSERVATION"
    assert changed.value.error.execution_state == "unknown"
    assert backend.dispatches == 1 and core.lease is None and not core.observations


def test_foreground_change_away_and_back_invalidates_lease() -> None:
    core, backend, context, payload = prepare()
    backend.foreground_tick = lambda: 2  # type: ignore[method-assign]
    with pytest.raises(RACPError) as changed:
        core.execute("desktop.type", payload, context)
    assert changed.value.error.code == "STALE_OBSERVATION" and backend.dispatches == 0
