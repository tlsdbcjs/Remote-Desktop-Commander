import asyncio
import json
import subprocess
import sys
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from racp_sdk.security import SecretStore
from test_re_provider import open_analysis, ready


@pytest.mark.re_plugin
async def test_re_tagged_query_through_actual_mcp_and_cli(
    live: dict[str, Any], tmp_path: Any
) -> None:
    import httpx

    await ready(live)
    opened = await open_analysis(live)
    id = opened["result"]["analysis_id"]
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as transport:
        async with streamable_http_client(live["url"] + "/mcp/", http_client=transport) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()
                result = await client.call_tool(
                    "re_query",
                    {
                        "device_id": live["device_id"],
                        "payload": {"analysis_id": id, "action": "info"},
                    },
                )
                assert not result.is_error, result
                text = "".join(item.text for item in result.content if item.type == "text")
                assert "synthetic-x86_64" in text
    owner = tmp_path / "owner.bin"
    SecretStore(owner).save({"token": live["owner"]})
    cli = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "racp_cli.main",
        "--gateway",
        live["url"],
        "--owner-store",
        str(owner),
        "--json",
        "re",
        "query",
        live["device_id"],
        "--payload-json",
        json.dumps({"analysis_id": id, "action": "functions", "limit": 1}),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(cli.communicate(), 10)
    assert cli.returncode == 0, stderr
    assert len(json.loads(stdout)["result"]["items"]) == 1
