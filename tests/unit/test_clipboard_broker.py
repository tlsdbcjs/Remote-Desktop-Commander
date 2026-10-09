import pytest
from racp_agent.broker.core import BrokerContext, BrokerCore
from racp_domain.models import RACPError
from test_desktop_fences import Backend


class ClipboardBackend(Backend):
    def __init__(self):
        super().__init__()
        self.clipboard_calls = []
        self.lock_during_read = False

    def clipboard(self, operation, payload):
        self.clipboard_calls.append((operation, payload))
        if self.lock_during_read:
            self.active = False
        return {"sequence_number": 12, "text": "owned fixture"}


def context():
    return BrokerContext(owner_id="owner_owned", device_id="dev_owned", operation_id="op_clip")


def test_clipboard_uses_session_gate_without_foreground_or_input_lease() -> None:
    backend = ClipboardBackend()
    core = BrokerCore(1, backend)
    result = core.execute("clipboard.read", {"session_id": 1}, context())
    assert result["sequence_number"] == 12 and core.lease is None
    core.execute(
        "clipboard.write", {"session_id": 1, "text": "owned", "expected_sequence": 12}, context()
    )
    assert len(backend.clipboard_calls) == 2 and backend.dispatches == 0


@pytest.mark.parametrize("state", ["wrong_session", "locked"])
def test_clipboard_cannot_access_other_or_locked_session(state: str) -> None:
    backend = ClipboardBackend()
    core = BrokerCore(1, backend)
    backend.active = state != "locked"
    with pytest.raises(RACPError) as raised:
        core.execute(
            "clipboard.read", {"session_id": 2 if state == "wrong_session" else 1}, context()
        )
    assert raised.value.error.code == (
        "PERMISSION_DENIED" if state == "wrong_session" else "SESSION_LOCKED"
    )
    assert not backend.clipboard_calls


@pytest.mark.parametrize("operation", ["clipboard.read", "clipboard.state"])
def test_read_cannot_return_bytes_if_session_locks_during_native_read(operation: str) -> None:
    backend = ClipboardBackend()
    backend.lock_during_read = True
    core = BrokerCore(1, backend)
    with pytest.raises(RACPError) as raised:
        core.execute(operation, {"session_id": 1}, context())
    assert raised.value.error.code == "SESSION_LOCKED"
