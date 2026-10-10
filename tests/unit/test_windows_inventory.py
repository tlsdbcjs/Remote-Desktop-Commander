import os
import uuid

import pytest
from racp_agent.providers.windows_inventory import (
    registry_text,
    service_record,
    services_list,
    software_list,
)
from racp_domain.models import RACPError
from racp_protocol.permissions import required_permissions
from racp_protocol.registry import REGISTRY, validate_payload


@pytest.mark.parametrize("operation", ["services.list", "software.inventory"])
def test_inventory_is_bounded_windows_only_and_requires_its_own_grant(operation):
    for payload in [
        {"limit": True},
        {"limit": 201},
        {"offset": -1},
        {"offset": 10001},
        {"command": "arbitrary"},
    ]:
        with pytest.raises(RACPError):
            validate_payload(operation, payload)
    assert REGISTRY[operation].minimum_os == ("Windows",)
    assert required_permissions(operation, {}) == (
        "services.read" if operation == "services.list" else "software.inventory.read",
    )


def test_service_metadata_does_not_read_binary_arguments_or_account():
    class Service:
        def name(self):
            return "own"

        def display_name(self):
            return "Own service"

        def status(self):
            return "stopped"

        def start_type(self):
            return "manual"

        def as_dict(self):
            raise AssertionError("Command line would be exposed")

    assert set(service_record(Service())) == {
        "name",
        "display_name",
        "status",
        "start_type",
        "availability",
    }
    for name in ["", "../outside", "bad\\name", "bad\nname"]:
        with pytest.raises(RACPError):
            validate_payload("services.get", {"name": name})


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registry and SCM")
def test_native_registry_reads_are_bounded_and_inventory_is_paginated():
    import winreg

    path = "Software\\RACP-Inventory-Test-" + uuid.uuid4().hex
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "RACP_OWN_METADATA")
            winreg.SetValueEx(key, "Oversized", 0, winreg.REG_SZ, "a" * 4096)
            winreg.SetValueEx(key, "EmbeddedNul", 0, winreg.REG_SZ, "own\x00hidden")
            winreg.SetValueEx(key, "Binary", 0, winreg.REG_BINARY, b"own")
            assert registry_text(key, "DisplayName") == "RACP_OWN_METADATA"
            for name in ["Oversized", "EmbeddedNul", "Binary", "Absent"]:
                assert registry_text(key, name) is None
        for operation, function in [
            ("services.list", services_list),
            ("software.inventory", software_list),
        ]:
            value = function(validate_payload(operation, {"limit": 1}))
            assert len(value["items"]) <= 1
            assert value["consistency"] == "live_observation"
            assert value["next_offset"] in {None, 1}
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
