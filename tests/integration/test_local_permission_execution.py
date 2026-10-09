"""Real Gateway/Agent RPCs must obey the Client ceiling even in trusted mode."""

import os
from typing import Any

import pytest
from racp_policy.permissions import LocalPermissions, compile_permissions


async def call(
    live: dict[str, Any], operation: str, payload: dict[str, Any], key: str = "local-gate"
) -> dict[str, Any]:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": operation,
            "payload": payload,
            "idempotency_key": key,
            "execution_profile_id": "trusted_personal",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_trusted_request_cannot_bypass_local_write_off(live: dict[str, Any]) -> None:
    live["agent"].permissions = compile_permissions(
        LocalPermissions(grants={"files.list": "allow"})
    )
    result = await call(
        live, "filesystem.write", {"path": "must-not-exist.txt", "content": "blocked"}
    )
    assert result["state"] == "FAILED" and result["error"]["code"] == "PERMISSION_DENIED"
    assert not (live["workspace"] / "must-not-exist.txt").exists()


async def test_local_approval_cannot_be_satisfied_by_trusted_profile(live: dict[str, Any]) -> None:
    live["agent"].permissions = compile_permissions(
        LocalPermissions(grants={"files.read.text": "require_approval"})
    )
    (live["workspace"] / "read.txt").write_text("private-fixture")
    result = await call(live, "filesystem.read", {"path": "read.txt"})
    assert result["state"] == "FAILED" and result["error"]["code"] == "APPROVAL_REQUIRED"
    assert "private-fixture" not in str(result)


async def test_process_observation_without_arguments_permission_redacts_cmdline(
    live: dict[str, Any],
) -> None:
    live["agent"].permissions = compile_permissions(
        LocalPermissions(grants={"process.inspect": "allow"})
    )
    result = await call(live, "process.inspect", {"pid": os.getpid()})
    assert result["state"] == "SUCCEEDED"
    assert result["result"]["cmdline"] is None
    assert result["result"]["arguments_redacted"] is True


async def test_binary_export_requires_both_read_and_export_leaves(live: dict[str, Any]) -> None:
    live["agent"].permissions = compile_permissions(
        LocalPermissions(grants={"files.read.binary": "allow"})
    )
    (live["workspace"] / "sample.bin").write_bytes(b"RACP-PRIVATE-BINARY")
    result = await call(live, "filesystem.read", {"path": "sample.bin", "binary": True})
    assert result["state"] == "FAILED" and result["error"]["code"] == "PERMISSION_DENIED"
    assert result["result"] is None


async def test_revocation_during_provider_await_suppresses_result(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = live["agent"]
    agent.permissions = compile_permissions(LocalPermissions(grants={"files.read.text": "allow"}))
    (live["workspace"] / "read.txt").write_text("revoked-private-fixture")
    original = agent.filesystem.execute

    async def revoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = await original(*args, **kwargs)
        agent.permissions = compile_permissions(LocalPermissions())
        return result

    monkeypatch.setattr(agent.filesystem, "execute", revoke)
    result = await call(live, "filesystem.read", {"path": "read.txt"})
    assert result["state"] == "FAILED" and result["error"]["code"] == "PERMISSION_DENIED"
    assert "revoked-private-fixture" not in str(result)
