"""Hardware metadata boundaries; no physical devices or native SDK calls."""
import pytest
from racp_domain.models import RACPError
from racp_protocol.permissions import required_permissions
from racp_protocol.registry import REGISTRY, validate_payload


def target() -> dict[str, str]:
    return {"agent_device_id": "dev_fixture", "agent_boot_id": "boot_fixture",
            "hardware_id": "hardware_" + "a" * 32, "interface_id": "hwif_" + "b" * 32,
            "connection_generation": "connection_" + "c" * 32}


def test_metadata_uses_bounded_opaque_targets_and_separate_permission() -> None:
    assert validate_payload("hardware.enumerate", {}) == {"limit": 100}
    assert validate_payload("hardware.inspect", {"target": target()}) == {"target": target()}
    for name in ("hardware.enumerate", "hardware.inspect"):
        assert required_permissions(name, {}) == ("hardware.inventory.read",)
        assert REGISTRY[name].minimum_os == ("Windows",)
        assert not REGISTRY[name].side_effect
    for payload in ({"limit": 0}, {"limit": 257}, {"limit": True}, {"device_path": "private"}):
        with pytest.raises(RACPError):
            validate_payload("hardware.enumerate", payload)
    with pytest.raises(RACPError):
        validate_payload("hardware.inspect", {"target": {**target(), "raw_handle": 1}})
    with pytest.raises(RACPError):
        validate_payload("hardware.open", {"target": target()})
