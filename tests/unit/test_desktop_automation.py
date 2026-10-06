import time
from types import SimpleNamespace
from typing import Any

import pytest
from racp_agent.broker.automation import Automation, ElementReference
from racp_agent.broker.identity import Identity
from racp_domain.models import RACPError
from racp_protocol.desktop import DesktopKey


@pytest.mark.parametrize(
    "change,code",
    [
        ("expired", "STALE_OBSERVATION"),
        ("wrong_observation", "STALE_OBSERVATION"),
        ("wrong_window", "STALE_OBSERVATION"),
        ("geometry", "STALE_OBSERVATION"),
        ("pid_reused", "STALE_OBSERVATION"),
        ("disabled", "STALE_OBSERVATION"),
        ("password", "PERMISSION_DENIED"),
        ("detached", "STALE_OBSERVATION"),
        ("elevated", "INTEGRITY_LEVEL_MISMATCH"),
        ("unreadable_identity", "PERMISSION_DENIED"),
    ],
)
def test_observed_ui_reference_cannot_mutate_changed_or_unscoped_target(
    monkeypatch: pytest.MonkeyPatch, change: str, code: str
) -> None:
    identity = Identity(123, 10, "S-1-5-21-1-2-3-1001", 1, 8192)
    native = Automation.__new__(Automation)
    native.identity = identity
    native.com = SimpleNamespace(COMError=OSError)
    root, element = object(), object()
    fingerprint: dict[str, Any] = {
        "pid": 123,
        "process_created": 10,
        "bounds": [0, 0, 20, 20],
        "enabled": True,
        "offscreen": False,
        "password": False,
    }
    reference = ElementReference(
        element, root, fingerprint.copy(), "window_test", time.monotonic() + 5
    )
    native.references = {"observation_test": {"element_test": reference}}
    payload = {
        "observation_id": "observation_test",
        "element_ref": "element_test",
        "expected_window_id": "window_test",
        "value": "blocked",
    }
    effects: list[str] = []
    native.fingerprint = lambda element: fingerprint.copy()  # type: ignore[method-assign]
    native.pattern = lambda element, name: SimpleNamespace(  # type: ignore[method-assign]
        CurrentIsReadOnly=False, SetValue=lambda value: effects.append(value)
    )
    native.client = SimpleNamespace(CompareElements=lambda a, b: a is b)
    native.walker = SimpleNamespace(GetParentElement=lambda element: root)
    if change == "expired":
        reference.expires = time.monotonic() - 1
    elif change == "wrong_observation":
        payload["observation_id"] = "observation_other"
    elif change == "wrong_window":
        payload["expected_window_id"] = "window_other"
    elif change == "geometry":
        fingerprint["bounds"] = [1, 0, 20, 20]
    elif change == "pid_reused":
        fingerprint["process_created"] += 1
    elif change == "disabled":
        fingerprint["enabled"] = False
    elif change == "password":
        fingerprint["password"] = reference.fingerprint["password"] = True
    elif change == "detached":
        native.walker.GetParentElement = lambda element: None
    elif change == "elevated":
        identity = Identity(123, 10, identity.sid, 1, 12288)
    elif change == "unreadable_identity":
        fingerprint["process_created"] = reference.fingerprint["process_created"] = None
    monkeypatch.setattr("racp_agent.broker.automation.process_identity", lambda pid: identity)
    with pytest.raises(RACPError) as refused:
        native.action("desktop.set_value", payload, lambda: None)
    assert refused.value.error.code == code and effects == []


def test_duplicate_chord_keys_are_rejected_before_dispatch() -> None:
    with pytest.raises(ValueError):
        DesktopKey(
            session_id=1,
            lease_id="lease_test",
            observation_id="observation_test",
            layout_revision="layout_test",
            expected_window_id="window_test",
            keys=["CTRL", "CTRL"],
        )
