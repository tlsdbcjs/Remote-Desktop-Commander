"""Actual MCP/RACP dispatch to Windows DbgHelp and Artifact recovery."""

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import httpx2
import psutil
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_policy.permissions import LocalPermissions, compile_permissions

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DbgHelp")


async def test_dump_rpc_mcp_uses_real_native_worker_and_separate_export_grant(
    live: dict[str, Any], tmp_path: Path
) -> None:
    assert hasattr(live["agent"], "process_dump"), "Dump is not wired into Agent dispatch"
    provider = live["agent"].process_dump
    target = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        "import time;time.sleep(60)",
        creationflags=0x08000000,
    )
    try:
        payload = {
            "pid": target.pid,
            "create_time": psutil.Process(target.pid).create_time(),
            "agent_boot_id": live["agent"].boot_id,
            "max_bytes": 16 * 1024**2,
        }
        body = {
            "device_id": live["device_id"],
            "operation": "process.dump",
            "payload": payload,
            "execution_profile_id": "trusted_personal",
            "idempotency_key": "dump-denied",
        }
        for grants in ({}, {"memory.dump.create": "allow"}, {"artifacts.export": "allow"}):
            live["agent"].permissions = compile_permissions(LocalPermissions(grants=grants))
            body["idempotency_key"] += "x"
            denied = await live["client"].post("/api/v1/operations", json=body)
            assert denied.status_code == 200, denied.text
            assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
            assert not provider.pending and not provider.protected_pids()
        live["agent"].permissions = compile_permissions(
            LocalPermissions(grants={"memory.dump.create": "allow", "artifacts.export": "allow"})
        )
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
            async with Client(
                streamable_http_client(live["url"] + "/mcp/", http_client=http)
            ) as client:
                tools = await client.list_tools()
                assert any(tool.name == "process_dump" for tool in tools.tools)
                result = await client.call_tool(
                    "process_dump",
                    {
                        **payload,
                        "device_id": live["device_id"],
                        "execution_profile_id": "trusted_personal",
                        "idempotency_key": "dump-mcp",
                    },
                )
                assert not result.is_error, result.structured_content
                value = result.structured_content["result"]
        artifact = await live["client"].get(
            "/api/v1/artifacts/" + value["artifact_id"] + "/content"
        )
        assert artifact.status_code == 200 and artifact.content[:4] == b"MDMP"
        assert hashlib.sha256(artifact.content).hexdigest() == value["sha256"]
        assert len(artifact.content) == value["bytes"]
        assert value["target_pid"] == target.pid and value["device_id"] == live["device_id"]
        assert value["cleanup_status"] == "complete" and target.returncode is None
        assert not provider.pending and not provider.protected_pids()
    finally:
        target.terminate()
        await asyncio.wait_for(target.wait(), 5)
