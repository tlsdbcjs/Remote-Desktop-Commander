"""MCP/RACP authorization with a scoped fixture Broker. Native API proof is separate."""

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_domain.models import RACPError
from racp_policy.permissions import LocalPermissions, compile_permissions
from racp_protocol.models import Heartbeat


class FixtureBroker:
    def __init__(self):
        self.process = SimpleNamespace(returncode=None)
        self.status = {"available": True}
        self.lock = asyncio.Lock()
        self.text, self.sequence = "owned clipboard fixture", 7
        self.calls = 0

    async def rpc(self, operation, payload, context, deadline):
        self.calls += 1
        assert payload["session_id"] == 1 and context["device_id"]
        if operation == "clipboard.state":
            return {"sequence_number": self.sequence, "session_id": 1}
        if operation == "clipboard.read":
            return {
                "text": self.text,
                "sequence_number": self.sequence,
                "returned_bytes": len(self.text.encode()),
                "truncated": False,
                "session_id": 1,
            }
        if payload["expected_sequence"] != self.sequence:
            raise RACPError("PRECONDITION_FAILED", "Fixture sequence changed", layer="broker")
        self.text = payload["text"]
        self.sequence += 1
        return {
            "sequence_number": self.sequence,
            "written_bytes": len((self.text or "").encode()),
            "cleared": self.text is None,
            "session_id": 1,
        }

    async def abort(self):
        pass

    def protected_pids(self):
        return set()


async def test_clipboard_mcp_uses_leaf_permission_and_provenance_without_desktop_input(
    live: dict[str, Any],
) -> None:
    agent = live["agent"]
    assert hasattr(agent.desktop, "clipboard_capability"), "Clipboard not wired to Agent"
    broker = FixtureBroker()
    agent.desktop.sessions[1] = broker
    await agent.send(
        Heartbeat(
            device_id=agent.device_id,
            agent_boot_id=agent.boot_id,
            connection_epoch=agent.epoch,
            capabilities=[agent.desktop.clipboard_capability()],
        )
    )
    for _ in range(50):
        info = (await live["client"].get("/api/v1/devices/" + agent.device_id)).json()
        if any(c["name"] == "clipboard" and c["enabled"] for c in info["info"]["capabilities"]):
            break
        await asyncio.sleep(0.02)
    body = {
        "device_id": live["device_id"],
        "operation": "clipboard.read",
        "payload": {"session_id": 1},
        "execution_profile_id": "trusted_personal",
        "idempotency_key": "clip-denied",
    }
    agent.permissions = compile_permissions(
        LocalPermissions(grants={"desktop.capture.screen": "allow"})
    )
    denied = await live["client"].post("/api/v1/operations", json=body)
    assert denied.status_code == 200 and denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert broker.calls == 0
    agent.permissions = compile_permissions(
        LocalPermissions(grants={"clipboard.text.read": "allow"})
    )
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            read = await client.call_tool(
                "clipboard_read",
                {
                    "device_id": agent.device_id,
                    "session_id": 1,
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "clip-read",
                },
            )
            assert not read.is_error, read.structured_content
            value = read.structured_content["result"]
            assert value["text"] == broker.text and value["device_id"] == agent.device_id
            assert value["agent_boot_id"] == agent.boot_id and value["observed_at"]
            write = await client.call_tool(
                "clipboard_write",
                {
                    "device_id": agent.device_id,
                    "session_id": 1,
                    "text": "must be denied",
                    "expected_sequence": 7,
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "clip-write-denied",
                },
            )
            assert write.is_error and broker.calls == 1
            agent.permissions = compile_permissions(
                LocalPermissions(grants={"clipboard.text.write": "allow"})
            )
            metadata = await client.call_tool(
                "clipboard_state",
                {
                    "device_id": agent.device_id,
                    "session_id": 1,
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "clip-state",
                },
            )
            assert not metadata.is_error and "text" not in metadata.structured_content["result"]
            written = await client.call_tool(
                "clipboard_write",
                {
                    "device_id": agent.device_id,
                    "session_id": 1,
                    "text": "owned replacement",
                    "expected_sequence": 7,
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "clip-write",
                },
            )
            assert not written.is_error and broker.text == "owned replacement", (
                written.structured_content
            )
            stale = await client.call_tool(
                "clipboard_write",
                {
                    "device_id": agent.device_id,
                    "session_id": 1,
                    "text": "must not replace",
                    "expected_sequence": 7,
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "clip-stale",
                },
            )
            assert (
                stale.is_error
                and stale.structured_content["error"]["code"] == "PRECONDITION_FAILED"
            )
            assert broker.text == "owned replacement"
