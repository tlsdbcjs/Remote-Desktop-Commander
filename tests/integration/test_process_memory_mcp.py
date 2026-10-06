"""A collects B memory through real MCP and retrieves the authenticated Artifact."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def test_mcp_memory_read_and_artifact_roundtrip(live: dict[str, Any]) -> None:
    if os.name != "nt":
        pytest.skip("Windows process memory")
    script = (
        "import ctypes,json,os,psutil,sys; data=bytes(range(256))*512; "
        "buffer=ctypes.create_string_buffer(data); "
        "print(json.dumps(dict(pid=os.getpid(),create_time=psutil.Process().create_time(),"
        "address=hex(ctypes.addressof(buffer)),size_bytes=len(data))),flush=True); "
        "sys.stdin.readline()"
    )
    target = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        descriptor = json.loads(await asyncio.wait_for(target.stdout.readline(), 10))
        descriptor["agent_boot_id"] = live["agent"].boot_id
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
            async with Client(
                streamable_http_client(live["url"] + "/mcp/", http_client=http)
            ) as client:
                result = await client.call_tool(
                    "process_memory_read",
                    {
                        "device_id": live["device_id"],
                        "execution_profile_id": "trusted_personal",
                        **descriptor,
                    },
                )
                assert not result.is_error, result
                output = result.structured_content["result"]
                response = await live["client"].get(
                    "/api/v1/artifacts/" + output["artifact_id"] + "/content"
                )
                assert response.status_code == 200
                assert response.content == bytes(range(256)) * 512
                assert output["sha256"] == hashlib.sha256(response.content).hexdigest()
    finally:
        target.stdin.close()
        try:
            await asyncio.wait_for(target.wait(), 5)
        except TimeoutError:
            target.terminate()
            await asyncio.wait_for(target.wait(), 5)
