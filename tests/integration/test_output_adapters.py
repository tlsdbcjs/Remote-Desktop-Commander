import asyncio
import json
import subprocess
import sys
from typing import Any

import httpx2
from conftest import shell_request
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_sdk.security import SecretStore


async def test_cli_and_mcp_expose_resolutions_and_completed_outputs(live: dict[str, Any]) -> None:
    request = shell_request(live, [sys.executable, "-c", "print('x'*100000)"], "adapter-output")
    operation = (await live["client"].post("/api/v1/operations", json=request)).json()
    operation_id = operation["operation_id"]
    store = live["workspace"].parent / "adapter-owner.bin"
    SecretStore(store).save({"token": live["owner"], "gateway": live["url"]})
    for action in ("outputs", "resolutions"):
        response = await live["client"].get(f"/api/v1/operations/{operation_id}/{action}")
        cli = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "racp_cli.main",
                "--gateway",
                live["url"],
                "--owner-store",
                str(store),
                "--json",
                "operation",
                action,
                operation_id,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        assert cli.returncode == 0 and json.loads(cli.stdout) == response.json()
        assert live["owner"] not in cli.stdout and live["credential"] not in cli.stdout
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            for action in ("outputs", "resolutions"):
                response = await live["client"].get(f"/api/v1/operations/{operation_id}/{action}")
                observed = await client.call_tool(
                    "operation_" + action, {"operation_id": operation_id}
                )
                assert not observed.is_error and observed.structured_content == response.json()


async def test_shell_non_utf8_decoding_still_limits_inline_utf8_bytes(live: dict[str, Any]) -> None:
    code = (
        "import sys; sys.stdout.buffer.write(b'\\xff'*40000); "
        "sys.stderr.buffer.write(b'\\xff'*40000)"
    )
    request = shell_request(live, [sys.executable, "-c", code], "inline-utf8-budget")
    request["payload"]["encoding"] = "latin-1"
    outcome = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert outcome["state"] == "SUCCEEDED", outcome
    result = outcome["result"]
    assert len(result["stdout"].encode()) + len(result["stderr"].encode()) <= 65536
    assert result["stdout"] == result["stderr"] == "ÿ" * 16384
    assert result["truncated"] and result["artifact_id"]
    assert result["spooled_bytes"] == 80000 and not result["artifact_truncated"]
