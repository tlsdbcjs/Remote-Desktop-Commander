import base64

import pytest
from pydantic import ValidationError
from racp_protocol.native_duplex import DuplexData, DuplexScope, parse_duplex_frame


def scope() -> DuplexScope:
    return DuplexScope(
        session_id="native_own",
        device_id="dev_own",
        agent_boot_id="boot_own",
        connection_epoch=1,
        principal_id="owner",
        workspace_id="default",
        permission_revision="a" * 64,
    )


def test_scope_is_frozen_and_every_authority_changes_fingerprint() -> None:
    value = scope()
    for field, replacement in {
        "session_id": "other",
        "device_id": "other",
        "agent_boot_id": "other",
        "connection_epoch": 2,
        "principal_id": "other",
        "workspace_id": "other",
        "permission_revision": "b" * 64,
    }.items():
        assert value.fingerprint != value.model_copy(update={field: replacement}).fingerprint
    with pytest.raises(ValidationError):
        value.connection_epoch = 2


@pytest.mark.parametrize(
    "change",
    [
        {"channel_id": True},
        {"channel_id": 0},
        {"channel_id": 17},
        {"byte_offset": -1},
        {"byte_offset": True},
        {"data_base64": "??"},
        {"data_base64": "Zh=="},
        {"data_base64": ""},
        {"data_base64": base64.b64encode(b"x" * 16385).decode()},
        {"peer": "192.168.29.121"},
    ],
)
def test_data_rejects_unbounded_noncanonical_or_untyped_wire(change: dict) -> None:
    payload = {
        "type": "native.data",
        "scope_fingerprint": scope().fingerprint,
        "channel_id": 1,
        "byte_offset": 0,
        "data_base64": "AA==",
    }
    with pytest.raises(ValidationError):
        parse_duplex_frame({**payload, **change})


def test_binary_payload_has_no_text_conversion() -> None:
    raw = bytes(range(256)) * 64
    data = DuplexData(
        scope_fingerprint=scope().fingerprint,
        channel_id=1,
        byte_offset=0,
        data_base64=base64.b64encode(raw).decode(),
    )
    assert data.decoded == raw
    assert parse_duplex_frame(data.model_dump()) == data
