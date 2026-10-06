import asyncio
import socket
import subprocess
import sys
from contextlib import aclosing
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import pytest_asyncio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_agent.runtime import Agent
from racp_gateway.store import GatewayStore
from racp_sdk.artifacts import ArtifactClient
from racp_sdk.security import digest, tls_context, token
from racp_sdk.terminal import TerminalStreamClient
from test_filesystem_process import remote
from test_terminal_streams import terminal
from tls_fixture import certificates

pytestmark = pytest.mark.remote_tls


@pytest_asyncio.fixture
async def live(tmp_path: Path) -> Any:
    ca, cert, key = certificates(tmp_path / "tls")
    data = tmp_path / "gateway"
    store = GatewayStore(data / "gateway.db")
    owner = token()
    store.initialize(digest(owner))
    enrolled = store.enroll(store.enrollment("TLS remote workflow"))
    store.close()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    gateway = f"https://localhost:{port}"
    command = [
        sys.executable,
        "-m",
        "racp_gateway.main",
        "--data-dir",
        str(data),
        "--port",
        str(port),
        "--host",
        "0.0.0.0",
        "--public-origin",
        gateway,
        "--tls-cert",
        str(cert),
        "--tls-key",
        str(key),
        "--enable-trusted-personal",
    ]
    log = (tmp_path / "gateway.log").open("wb")

    async def start() -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *command,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    process = await start()
    agent_task = None
    agent = None
    try:
        async with httpx.AsyncClient(
            base_url=gateway,
            headers={"Authorization": "Bearer " + owner},
            verify=tls_context(ca),
            timeout=20,
        ) as http:

            async def ready() -> None:
                for _ in range(300):
                    assert process.returncode is None, (tmp_path / "gateway.log").read_text(
                        errors="replace"
                    )
                    try:
                        if (await http.get("/readyz")).status_code == 200:
                            return
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.05)
                pytest.fail("TLS Gateway did not become ready")

            await ready()
            workspace = tmp_path / "remote workspace"
            workspace.mkdir()
            agent = Agent(
                gateway,
                enrolled["credential"],
                enrolled["device_id"],
                workspace,
                tmp_path / "agent",
                ca_file=ca,
                profile="trusted_personal",
            )
            agent_task = asyncio.create_task(agent.run())

            async def online() -> None:
                for _ in range(300):
                    device = (await http.get("/api/v1/devices/" + enrolled["device_id"])).json()
                    if device["info"].get("status") == "ONLINE":
                        return
                    if agent_task.done():
                        await agent_task
                    await asyncio.sleep(0.05)
                pytest.fail("Agent did not connect over WSS")

            await online()

            async def restart() -> None:
                nonlocal process
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
                process = await start()
                await ready()
                await online()

            yield {
                "client": http,
                "agent": agent,
                "agent_task": agent_task,
                "workspace": workspace,
                "device_id": enrolled["device_id"],
                "credential": enrolled["credential"],
                "owner": owner,
                "url": gateway,
                "ca_file": ca,
                "restart_gateway": restart,
            }
    finally:
        if agent_task is not None and agent is not None:
            agent.stopping.set()
            agent_task.cancel()
            await asyncio.gather(agent_task, return_exceptions=True)
        if process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
        log.close()


async def test_https_mcp_reads_writes_and_executes_on_connected_agent(live: dict[str, Any]) -> None:
    assert live["url"].startswith("https://")
    async with httpx.AsyncClient() as untrusted:
        with pytest.raises(httpx.ConnectError):
            await untrusted.get(live["url"] + "/readyz")
    async with httpx2.AsyncClient(
        verify=tls_context(live["ca_file"]),
        headers={"Authorization": "Bearer " + live["owner"], "Origin": live["url"]},
    ) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            devices = await client.call_tool("device_list", {})
            assert live["device_id"] in str(devices.structured_content)
            written = await client.call_tool(
                "fs_write",
                {
                    "device_id": live["device_id"],
                    "path": "remote-notes.txt",
                    "content": "원격 PC 자료\n",
                    "idempotency_key": "remote-tls-write",
                    "execution_profile_id": "trusted_personal",
                },
            )
            assert not written.is_error, written
            read = await client.call_tool(
                "fs_read", {"device_id": live["device_id"], "path": "remote-notes.txt"}
            )
            assert read.structured_content["result"]["text"] == "원격 PC 자료\n"
            executed = await client.call_tool(
                "shell_exec",
                {
                    "device_id": live["device_id"],
                    "idempotency_key": "remote-tls-shell",
                    "execution_profile_id": "trusted_personal",
                    "argv": [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-c",
                        "from pathlib import Path; "
                        "print(Path('remote-notes.txt').read_text(encoding='utf-8'))",
                    ],
                },
            )
            assert (
                not executed.is_error
                and "원격 PC 자료" in executed.structured_content["result"]["stdout"]
            )
    for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"}):
        rejected = await live["client"].get("/api/v1/devices", headers=headers)
        assert rejected.status_code in {400, 403}
    denied = await live["client"].get(
        "/api/v1/devices", headers={"Authorization": "Bearer " + live["credential"]}
    )
    assert denied.status_code == 401


async def test_https_artifact_roundtrip_and_wss_terminal_use_same_ca(
    live: dict[str, Any], tmp_path: Path
) -> None:
    contents = b"remote-binary\x00\xff" * 7000
    source = tmp_path / "local.bin"
    source.write_bytes(contents)
    transport = ArtifactClient(
        live["url"], live["owner"], live["device_id"], ca_file=live["ca_file"]
    )
    uploaded = await transport.upload(source)
    written = await remote(
        live, "filesystem.write", {"path": "received.bin", "artifact_id": uploaded["id"]}
    )
    assert written["state"] == "SUCCEEDED", written
    read = await remote(live, "filesystem.read", {"path": "received.bin", "binary": True})
    assert read["state"] == "SUCCEEDED", read
    downloaded = tmp_path / "downloaded.bin"
    await transport.download(read["result"]["artifact_id"], downloaded)
    assert downloaded.read_bytes() == contents
    opened = await terminal(
        live, "open", {"argv": [getattr(sys, "_base_executable", sys.executable), "-q", "-i"]}
    )
    handle = opened["handle_id"]
    try:
        await terminal(
            live, "write", {"handle_id": handle, "data": "print('REMOTE_TLS_TERMINAL')\r"}
        )
        stream = TerminalStreamClient(live["url"], live["owner"], ca_file=live["ca_file"])
        async with aclosing(stream.events(live["device_id"], handle)) as frames:
            async with asyncio.timeout(10):
                data = ""
                async for frame in frames:
                    if frame["type"] == "stream_data":
                        data += frame["data"]
                        if "REMOTE_TLS_TERMINAL" in data:
                            break
                assert "REMOTE_TLS_TERMINAL" in data
    finally:
        await terminal(live, "close", {"handle_id": handle})
    await live["restart_gateway"]()
    observed = await remote(
        live, "filesystem.read", {"path": "remote-notes-missing"}, profile="read_only"
    )
    assert observed["state"] == "FAILED" and not live["agent_task"].done()
