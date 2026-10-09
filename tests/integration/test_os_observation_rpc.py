"""Dedicated OS RPC/MCP observations work without granting arbitrary execution."""

import os
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_policy.permissions import LocalPermissions, compile_permissions
from racp_protocol.models import Context, Request, new_id, timestamp
from racp_sdk.security import canonical_digest, digest


async def test_resources_are_default_denied_then_allowed_without_shell(
    live: dict[str, Any],
) -> None:
    body = {
        "device_id": live["device_id"],
        "operation": "system.resources",
        "payload": {},
        "execution_profile_id": "read_only",
    }
    denied = await live["client"].post("/api/v1/operations", json=body)
    assert denied.status_code == 200
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    live["agent"].permissions = compile_permissions(
        LocalPermissions(grants={"system.resources.read": "allow"})
    )
    allowed = await live["client"].post("/api/v1/operations", json=body)
    assert allowed.status_code == 200 and allowed.json()["state"] == "SUCCEEDED"
    value = allowed.json()["result"]
    assert value["device_id"] == live["device_id"] and value["memory"]["total_bytes"] > 0
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            result = await client.call_tool("system_resources", {"device_id": live["device_id"]})
            assert not result.is_error, result
            assert result.structured_content["result"]["agent_boot_id"] == live["agent"].boot_id


@pytest.mark.skipif(os.name != "nt", reason="Windows inventory")
@pytest.mark.parametrize(
    "operation,grant",
    [("services.list", "services.read"), ("software.inventory", "software.inventory.read")],
)
async def test_windows_inventory_mcp_requires_explicit_grant_without_execution(
    live: dict[str, Any],
    operation: str,
    grant: str,
) -> None:
    body = {
        "device_id": live["device_id"],
        "operation": operation,
        "payload": {"limit": 1},
        "execution_profile_id": "read_only",
    }
    denied = await live["client"].post("/api/v1/operations", json=body)
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    live["agent"].permissions = compile_permissions(LocalPermissions(grants={grant: "allow"}))
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            result = await client.call_tool(
                operation.replace(".", "_"), {"device_id": live["device_id"], "limit": 1}
            )
            assert not result.is_error, result
            value = result.structured_content["result"]
            assert value["device_id"] == live["device_id"] and len(value["items"]) <= 1


async def test_mcp_marks_withheld_output_as_error_while_preserving_completed_state(
    live: dict[str, Any],
) -> None:
    request = Request(
        device_id=live["device_id"],
        agent_boot_id=live["agent"].boot_id,
        connection_epoch=live["agent"].epoch,
        request_id=new_id("req"),
        operation_id=new_id("op"),
        trace_id="0" * 32,
        timestamp=timestamp(),
        operation="filesystem.read",
        payload={"path": "retained.txt"},
        timeout_ms=1000,
        remaining_timeout_ms=1000,
        execution_mode="sync",
        idempotency_key="withheld-output-fixture",
        context=Context(
            principal_id="owner_local", execution_profile_id="read_only", policy_revision=1
        ),
    )
    store = live["app"].state.control.store
    record, _ = store.accept(
        digest("withheld-fixture"),
        digest(request.idempotency_key),
        canonical_digest(request.model_dump()),
        request.model_dump(),
    )
    store.transition(
        record["id"],
        "SUCCEEDED",
        error={
            "code": "PERMISSION_DENIED",
            "message": "output withheld",
            "execution_state": "completed",
            "details": {"output_authorization": "withheld"},
        },
    )
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            result = await client.call_tool("operation_get", {"operation_id": record["id"]})
            assert result.is_error
            assert result.structured_content["state"] == "SUCCEEDED"
            assert result.structured_content["error"]["code"] == "PERMISSION_DENIED"
