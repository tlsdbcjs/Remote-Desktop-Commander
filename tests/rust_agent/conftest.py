"""Subprocess-only Rust Agent fixture; production client never starts Python."""

import asyncio
import json
import os
import socket
import ssl
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from racp_gateway.app import create_app
from racp_gateway.store import GatewayStore
from racp_sdk.security import SecretStore, digest, token
from tls_fixture import certificates

from .support import ROOT, bridge


@pytest.fixture(scope="session")
def rust_agent() -> Path:
    executable = ROOT / "target" / "debug" / ("racp-agent.exe" if os.name == "nt" else "racp-agent")
    assert executable.exists(), "Build racp-agent before running integration tests"
    return executable


@pytest_asyncio.fixture
async def rust_live(tmp_path: Path, rust_agent: Path):
    gateway_dir = tmp_path / "gateway"
    store = GatewayStore(gateway_dir / "gateway.db")
    owner = token()
    store.initialize(digest(owner))
    enrolled = store.enroll(store.enrollment("Rust test device"))
    store.close()
    ca, certificate, key = certificates(tmp_path / "tls")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    gateway = f"https://localhost:{port}"
    app = create_app(gateway_dir, trusted_personal=True)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            lifespan="on",
            ws_per_message_deflate=False,
            ws_max_size=1024 * 1024,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    workspace = tmp_path / "한글 공백 workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    # Golden data is written by the existing Python SecretStore and read by Rust.
    settings = {
        "version": 1,
        "gateway": gateway,
        "device_id": enrolled["device_id"],
        "workspace": str(workspace),
        "data_dir": str(state / "data"),
        "profile": "trusted_personal",
        "ca_file": str(ca),
        "allowed_workspaces": [],
        "desktop_enabled": False,
    }
    SecretStore(state / "credential.bin").save(
        {
            "gateway": gateway,
            "device_id": enrolled["device_id"],
            "credential": enrolled["credential"],
            "agent_settings": json.dumps(settings),
            "ca_file": str(ca),
        }
    )
    async with httpx.AsyncClient(
        base_url=gateway,
        headers={"Authorization": "Bearer " + owner},
        verify=ssl.create_default_context(cafile=str(ca)),
        timeout=20,
        trust_env=False,
    ) as http:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        try:
            yield {
                "http": http,
                "gateway": gateway,
                "device_id": enrolled["device_id"],
                "state": state,
                "workspace": workspace,
                "executable": rust_agent,
                "ca": ca,
                "app": app,
            }
        finally:
            try:
                await bridge(rust_agent, state, "stop")
            except Exception:
                pass
            await http.aclose()
            server.should_exit = True
            await asyncio.wait_for(task, 10)
