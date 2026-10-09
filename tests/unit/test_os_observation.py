"""Typed OS observations must remain bounded, scoped, and independent of shell grants."""

import os
from pathlib import Path

import pytest
from racp_agent.providers.os_observation import OSObservationProvider
from racp_domain.models import ExecutionContext, RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.registry import validate_payload


def context() -> ExecutionContext:
    return ExecutionContext("op_os", "req_os", "0" * 32, "dev_os", "owner", "boot_os", 2000)


async def test_typed_system_resources_and_network_interfaces_have_origin(tmp_path: Path) -> None:
    provider = OSObservationProvider(tmp_path)
    for operation in (
        "system.info",
        "system.resources",
        "system.locale",
        "network.interfaces",
        "storage.volumes",
    ):
        payload = validate_payload(operation, {})
        result = await provider.execute(operation, payload, context())
        assert result["state"] == "SUCCEEDED"
        assert result["result"]["device_id"] == "dev_os"
        assert result["result"]["agent_boot_id"] == "boot_os"
        assert result["result"]["observed_at"]
        assert evaluate(operation, profile_rules("read_only")) == Decision.ALLOW


async def test_environment_query_never_reads_arbitrary_or_secret_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RACP_OWN_TEST_SECRET", "must-never-appear")
    provider = OSObservationProvider(tmp_path)
    payload = validate_payload("system.environment", {"keys": ["RACP_OWN_TEST_SECRET"]})
    with pytest.raises(RACPError) as raised:
        await provider.execute("system.environment", payload, context())
    assert raised.value.error.code == "PERMISSION_DENIED"
    assert "must-never-appear" not in str(raised.value)
    safe = validate_payload(
        "system.environment", {"keys": ["SystemRoot"] if os.name == "nt" else ["LANG"]}
    )
    assert (await provider.execute("system.environment", safe, context()))["state"] == "SUCCEEDED"


async def test_connections_are_bounded_and_pid_filter_is_preserved(tmp_path: Path) -> None:
    provider = OSObservationProvider(tmp_path)
    payload = validate_payload("network.connections", {"pid": os.getpid(), "limit": 1})
    result = (await provider.execute("network.connections", payload, context()))["result"]
    assert len(result["items"]) <= 1
    assert all(item["pid"] == os.getpid() for item in result["items"])
    assert result["consistency"] == "live_observation"


@pytest.mark.parametrize(
    "operation",
    ["system.environment", "network.connections", "network.interfaces", "storage.volumes"],
)
async def test_provider_accepts_valid_omitted_defaults(tmp_path: Path, operation: str) -> None:
    result = await OSObservationProvider(tmp_path).execute(operation, {}, context())
    assert result["state"] == "SUCCEEDED"
