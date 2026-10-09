"""MCP -> RACP -> actual authenticated pipe -> Win32 clipboard in a private station."""

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx2
import psutil
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_agent.broker.identity import process_identity
from racp_agent.broker.supervisor import BrokerSupervisor
from racp_policy.permissions import LocalPermissions, compile_permissions
from racp_protocol.models import Heartbeat

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows clipboard and Broker pipe")
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/private_clipboard_broker.py"


async def test_native_clipboard_mcp_roundtrip_budget_cas_and_owned_cleanup(
    live: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = process_identity(os.getpid()).session
    if not session_id:
        pytest.skip("Native interactive Broker requires a user session")
    agent = live["agent"]
    broker = BrokerSupervisor(tmp_path / "private-broker", session_id, agent.device_id)
    script = FIXTURE
    monkeypatch.setattr(
        broker, "_command", lambda path: [sys.executable, "-I", str(script), "--pairing", str(path)]
    )
    def no_gui_guard(_config):
        raise OSError("Private clipboard fixture has no GUI input backend")

    monkeypatch.setattr("racp_agent.broker.supervisor.start_guard", no_gui_guard)
    agent.desktop.sessions[session_id] = broker
    pids = set()
    try:
        await broker.start()
        pids.update(broker.protected_pids())
        assert broker.status["clipboard_scope"] == "owned_private_test_window_station"
        agent.permissions = compile_permissions(
            LocalPermissions(
                grants={
                    "clipboard.text.read": "allow",
                    "clipboard.text.write": "allow",
                }
            )
        )
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
            if any(c["name"] == "clipboard" and c["healthy"] for c in info["info"]["capabilities"]):
                break
            await asyncio.sleep(0.02)
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
            async with Client(
                streamable_http_client(live["url"] + "/mcp/", http_client=http)
            ) as client:

                async def call(name, **payload):
                    return await client.call_tool(
                        name,
                        {
                            "device_id": agent.device_id,
                            "session_id": session_id,
                            "execution_profile_id": "trusted_personal",
                            "idempotency_key": uuid.uuid4().hex,
                            **payload,
                        },
                    )

                read = await call("clipboard_read", max_bytes=8192)
                assert not read.is_error, read.structured_content
                value = read.structured_content["result"]
                assert value["text"] == "RACP_OWNED_PRIVATE_CLIP_한글🙂"
                sequence = value["sequence_number"]
                written = await call(
                    "clipboard_write", text="RACP_OWN_NEW_🙂", expected_sequence=sequence
                )
                assert not written.is_error, written.structured_content
                stale = await call(
                    "clipboard_write", text="must not replace", expected_sequence=sequence
                )
                assert (
                    stale.is_error
                    and stale.structured_content["error"]["code"] == "PRECONDITION_FAILED"
                )
                short = await call("clipboard_read", max_bytes=5)
                assert not short.is_error and short.structured_content["result"]["text"] == "RACP_"
                assert short.structured_content["result"]["truncated"]
                state = await call("clipboard_state")
                assert not state.is_error and "text" not in state.structured_content["result"]
                cleared = await call(
                    "clipboard_write",
                    clear=True,
                    expected_sequence=state.structured_content["result"]["sequence_number"],
                )
                assert not cleared.is_error, cleared.structured_content
    finally:
        async with broker.lock:
            await broker.stop()
        agent.desktop.sessions.pop(session_id, None)
        assert broker.cleanup_status == "complete" and all(
            not psutil.pid_exists(pid) for pid in pids
        )
