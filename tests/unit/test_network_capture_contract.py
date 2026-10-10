"""Capture is a scoped, sensitive recipe, never a shell permission alias."""

import pytest
from racp_domain.models import RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.permissions import required_permissions
from racp_protocol.registry import REGISTRY, validate_payload


def valid() -> dict:
    return {"local_ip": "192.168.29.121", "peer_ip": "192.168.29.141", "local_port": 50123}


def test_capture_contract_is_windows_sensitive_and_has_independent_export_gate() -> None:
    data = validate_payload("network.capture", valid())
    assert data["duration_ms"] == 3000 and data["max_bytes"] == 16777216
    assert set(required_permissions("network.capture", data)) == {
        "network.capture.ipv4",
        "artifacts.export",
    }
    assert REGISTRY["network.capture"].minimum_os == ("Windows",)
    assert REGISTRY["network.capture"].side_effect
    assert REGISTRY["network.capture"].retry_class == "journal_only"
    assert evaluate("network.capture", profile_rules("read_only")) == Decision.DENY
    assert evaluate("network.capture", profile_rules("standard")) == Decision.REQUIRE_APPROVAL
    assert evaluate("network.capture", profile_rules("trusted_personal")) == Decision.ALLOW


@pytest.mark.parametrize(
    "extra",
    [
        {"local_ip": "0.0.0.0"},
        {"peer_ip": "224.0.0.1"},
        {"peer_ip": "255.255.255.255"},
        {"peer_ip": "::1"},
        {"local_port": True},
        {"local_port": 0},
        {"duration_ms": 30001},
        {"max_bytes": 16777217},
        {"max_bytes": 24},
        {"output": "C:/arbitrary.pcap"},
        {"command": "cmd.exe"},
    ],
)
def test_capture_rejects_unscoped_or_unbounded_inputs(extra: dict) -> None:
    with pytest.raises(RACPError) as raised:
        validate_payload("network.capture", {**valid(), **extra})
    assert raised.value.error.code == "INVALID_ARGUMENT"
