"""Fresh PC command, protected saved settings, WSS, MCP workflow and restart."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import httpx2
import psutil
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_gateway.store import GatewayStore
from racp_sdk.security import SecretStore, digest, tls_context, token
from tls_fixture import certificates


async def terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    owned = []
    try:
        owned = [
            (p.pid, p.create_time()) for p in psutil.Process(process.pid).children(recursive=True)
        ]
    except psutil.NoSuchProcess:
        pass
    process.terminate()
    await asyncio.wait_for(process.wait(), 5)
    for pid, created in owned:
        try:
            child = psutil.Process(pid)
            if child.create_time() == created:
                child.kill()
        except psutil.NoSuchProcess:
            pass


async def test_fresh_pc_connects_over_tls_and_resumes_saved_settings(tmp_path: Path) -> None:
    ca, cert, key = certificates(tmp_path / "tls")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    gateway = f"https://localhost:{port}"
    data = tmp_path / "gateway"
    store = GatewayStore(data / "gateway.db")
    owner = token()
    store.initialize(digest(owner))
    store.close()
    workspace = tmp_path / "새 PC 자료"
    workspace.mkdir()
    documents = tmp_path / "추가 자료"
    documents.mkdir()
    (workspace / "자료.txt").write_bytes("원격 PC의 첫 자료\n".encode())
    environment = dict(os.environ)
    environment["LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"] = str(
        tmp_path / "user-state"
    )
    state = tmp_path / "user-state" / ("RACP/agent" if os.name == "nt" else "racp/agent")
    agent_process = None
    gateway_process = None
    device_id = ""
    with (
        (tmp_path / "gateway.log").open("wb") as gateway_log,
        (tmp_path / "agent.log").open("wb") as agent_log,
    ):
        try:
            gateway_process = await asyncio.create_subprocess_exec(
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
                stdout=gateway_log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            async with httpx.AsyncClient(
                base_url=gateway,
                verify=tls_context(ca),
                timeout=20,
                trust_env=False,
                headers={"Authorization": "Bearer " + owner},
            ) as http:
                for _ in range(300):
                    assert gateway_process.returncode is None
                    try:
                        if (await http.get("/readyz")).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("Gateway did not become ready")
                issued = (
                    await http.post("/api/v1/enrollment-tokens", json={"name": "새로 연결한 PC"})
                ).json()
                secret = issued["token"]
                agent_process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "racp_agent.connect",
                    "--gateway",
                    gateway,
                    "--workspace",
                    str(workspace),
                    "--allow-workspace",
                    "docs=" + str(documents),
                    "--ca-file",
                    str(ca),
                    "--token-stdin",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=agent_log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                assert agent_process.stdin is not None
                agent_process.stdin.write((secret + "\n").encode())
                await agent_process.stdin.drain()
                agent_process.stdin.close()

                async def connected(previous_boot: str = "") -> dict[str, Any]:
                    assert agent_process is not None
                    for _ in range(600):
                        assert agent_process.returncode is None, (tmp_path / "agent.log").read_text(
                            errors="replace"
                        )[-2000:]
                        devices = (await http.get("/api/v1/devices")).json()["items"]
                        if devices:
                            device = devices[0]
                            if (
                                device["info"].get("status") in {"ONLINE", "DEGRADED"}
                                and device["info"].get("agent_boot_id") != previous_boot
                            ):
                                return device
                        await asyncio.sleep(0.05)
                    pytest.fail("New PC did not reconcile its WSS connection")

                initial = await connected()
                device_id = initial["id"]
                credential_store = state / "credential.bin"
                saved = SecretStore(credential_store).load()
                assert saved["device_id"] == device_id and saved["ca_file"] == str(ca)
                assert json.loads(saved["agent_settings"])["profile"] == "read_only"
                assert json.loads(saved["agent_settings"])["allowed_workspaces"] == [
                    {"id": "docs", "path": str(documents)}
                ]
                assert (
                    await http.post("/agent/v1/enroll", json={"token": secret})
                ).status_code == 401
                async with httpx2.AsyncClient(
                    verify=tls_context(ca),
                    headers={"Authorization": "Bearer " + owner},
                ) as transport:
                    async with Client(
                        streamable_http_client(gateway + "/mcp/", http_client=transport)
                    ) as client:
                        result = await client.call_tool(
                            "fs_read", {"device_id": device_id, "path": "자료.txt"}
                        )
                        assert (
                            not result.is_error
                            and result.structured_content["result"]["text"] == "원격 PC의 첫 자료\n"
                        )
                        denied = await client.call_tool(
                            "fs_write",
                            {
                                "device_id": device_id,
                                "path": "denied.txt",
                                "content": "do not write",
                                "idempotency_key": "onboard-readonly",
                                "execution_profile_id": "trusted_personal",
                            },
                        )
                        assert denied.is_error and not (workspace / "denied.txt").exists()
                await terminate(agent_process)
                agent_process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "racp_agent.main",
                    "--profile",
                    "trusted_personal",
                    cwd=workspace,
                    stdout=agent_log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                restarted = await connected(initial["info"]["agent_boot_id"])
                assert restarted["id"] == device_id and restarted["epoch"] > initial["epoch"]
                assert SecretStore(credential_store).load() == saved
                async with httpx2.AsyncClient(
                    verify=tls_context(ca),
                    headers={"Authorization": "Bearer " + owner},
                ) as transport:
                    async with Client(
                        streamable_http_client(gateway + "/mcp/", http_client=transport)
                    ) as client:
                        written = await client.call_tool(
                            "fs_write",
                            {
                                "device_id": device_id,
                                "workspace_id": "docs",
                                "path": "saved.txt",
                                "content": "저장 후 실행\n",
                                "idempotency_key": "onboard-write",
                                "execution_profile_id": "trusted_personal",
                            },
                        )
                        assert not written.is_error, written
                        result = await client.call_tool(
                            "shell_exec",
                            {
                                "device_id": device_id,
                                "workspace_id": "docs",
                                "argv": [
                                    sys.executable,
                                    "-X",
                                    "utf8",
                                    "-c",
                                    "from pathlib import Path; "
                                    "print(Path('saved.txt').read_text(encoding='utf-8'))",
                                ],
                                "idempotency_key": "onboard-execute",
                                "execution_profile_id": "trusted_personal",
                            },
                        )
                        assert (
                            not result.is_error
                            and "저장 후 실행" in result.structured_content["result"]["stdout"]
                        )
                        assert (documents / "saved.txt").exists()
                        assert not (workspace / "saved.txt").exists()
                assert len((await http.get("/api/v1/devices")).json()["items"]) == 1
                agent_log.flush()
                recorded = (tmp_path / "agent.log").read_text(encoding="utf-8", errors="replace")
                assert '"event": "agent_connected"' in recorded
                assert (
                    secret not in recorded
                    and saved["credential"] not in recorded
                    and owner not in recorded
                )
        finally:
            await terminate(agent_process)
            await terminate(gateway_process)
