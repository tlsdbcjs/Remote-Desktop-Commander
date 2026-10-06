import asyncio
import hashlib
import os
import socket
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from racp_agent.plugins.config import PluginConfig, PluginInstallation
from racp_agent.plugins.gdb_installation import create_gdb_installation
from racp_agent.plugins.ghidra_installation import create_ghidra_installation
from racp_agent.runtime import Agent
from racp_agent.workspaces import WorkspaceSpec
from racp_gateway.app import create_app
from racp_gateway.store import GatewayStore
from racp_protocol.plugins import PluginManifest
from racp_protocol.reversing import RE_MODELS, RE_READS
from racp_sdk.security import digest, token


@pytest.fixture(autouse=True)
def reference_browser_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = Path(__file__).resolve().parents[1] / ".tools/playwright"
    if cache.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))


@pytest_asyncio.fixture
async def live(
    tmp_path: Path, request: pytest.FixtureRequest, re_installations: PluginConfig
) -> Any:
    selected_desktop_sessions = desktop_sessions(request)
    login_users: tuple[str, ...] = ()
    if request.node.get_closest_marker("desktop_login"):
        if os.name != "nt":
            pytest.skip("Windows user-login registration")
        from racp_agent.broker.identity import process_identity

        identity = process_identity(os.getpid())
        if identity.session == 0:
            pytest.skip("test requires a logged-on Windows user session")
        login_users = (identity.sid,)
    gateway_dir = tmp_path / "gateway"
    store = GatewayStore(gateway_dir / "gateway.db")
    secret = token()
    store.initialize(digest(secret))
    enrolled = store.enroll(store.enrollment("테스트 장비"))
    store.close()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app = create_app(gateway_dir, trusted_personal=True)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            lifespan="on",
            ws_per_message_deflate=False,
            ws_max_size=1024 * 1024,
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    gateway = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(
        base_url=gateway,
        headers={"Authorization": "Bearer " + secret},
        timeout=20,
    ) as client:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        workspace = tmp_path / "한글 공백 workspace"
        workspace.mkdir()
        additional = ()
        if request.node.get_closest_marker("workspaces"):
            documents = tmp_path / "두 번째 자료"
            documents.mkdir()
            additional = (WorkspaceSpec(id="docs", path=documents),)
        agent = Agent(
            gateway,
            enrolled["credential"],
            enrolled["device_id"],
            workspace,
            tmp_path / "agent",
            profile="trusted_personal",
            browser_cdp=request.node.get_closest_marker("browser_cdp") is not None,
            desktop_sessions=selected_desktop_sessions,
            desktop_login_users=login_users,
            plugins=re_installations,
            allowed_workspaces=additional,
        )
        agent_task = asyncio.create_task(agent.run())
        for _ in range(100):
            device = (await client.get("/api/v1/devices/" + enrolled["device_id"])).json()
            if device["info"].get("status") == "ONLINE":
                break
            if agent_task.done():
                await agent_task
            await asyncio.sleep(0.05)
        assert device["info"].get("status") == "ONLINE"
        state = {
            "client": client,
            "agent": agent,
            "app": app,
            "workspace": workspace,
            "workspaces": {"default": workspace, **{item.id: item.path for item in additional}},
            "device_id": enrolled["device_id"],
            "credential": enrolled["credential"],
            "owner": secret,
            "url": gateway,
            "agent_task": agent_task,
        }

        async def restart_gateway() -> None:
            nonlocal app, server, server_task
            server.should_exit = True
            await asyncio.wait_for(server_task, 10)
            app = create_app(gateway_dir, trusted_personal=True)
            restarted_listener = socket.socket()
            restarted_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            restarted_listener.bind(("127.0.0.1", port))
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
            server_task = asyncio.create_task(server.serve(sockets=[restarted_listener]))
            state["app"] = app
            for _ in range(200):
                if server.started:
                    device = (await client.get("/api/v1/devices/" + enrolled["device_id"])).json()
                    if device["info"].get("status") == "ONLINE":
                        return
                await asyncio.sleep(0.05)
            raise AssertionError("Agent did not reconnect to the restarted Gateway")

        state["restart_gateway"] = restart_gateway

        async def restart_agent() -> None:
            nonlocal agent, agent_task
            agent.stopping.set()
            agent_task.cancel()
            await asyncio.gather(agent_task, return_exceptions=True)
            agent = Agent(
                gateway,
                enrolled["credential"],
                enrolled["device_id"],
                workspace,
                tmp_path / "agent",
                profile="trusted_personal",
                browser_cdp=request.node.get_closest_marker("browser_cdp") is not None,
                desktop_sessions=selected_desktop_sessions,
                desktop_login_users=login_users,
                plugins=re_installations,
                allowed_workspaces=additional,
            )
            agent_task = asyncio.create_task(agent.run())
            state["agent"], state["agent_task"] = agent, agent_task
            for _ in range(200):
                device = (await client.get("/api/v1/devices/" + enrolled["device_id"])).json()
                if (
                    device["info"].get("status") == "ONLINE"
                    and device["info"].get("agent_boot_id") == agent.boot_id
                ):
                    return
                if agent_task.done():
                    await agent_task
                await asyncio.sleep(0.05)
            raise AssertionError("new Agent boot did not reconcile")

        state["restart_agent"] = restart_agent
        try:
            yield state
        finally:
            agent.stopping.set()
            agent_task.cancel()
            await asyncio.gather(agent_task, return_exceptions=True)
            server.should_exit = True
            await asyncio.wait_for(server_task, 10)


def desktop_sessions(request: pytest.FixtureRequest) -> tuple[int, ...]:
    if request.node.get_closest_marker("desktop") is None:
        return ()
    if os.name != "nt":
        pytest.skip("Windows interactive-session Broker")
    from racp_agent.broker.identity import process_identity

    session = process_identity(os.getpid()).session
    if session == 0:
        pytest.skip("foreground Broker requires a logged-on Windows user session")
    return (session,)


@pytest.fixture
def re_installations(tmp_path: Path, request: pytest.FixtureRequest) -> PluginConfig:
    if request.node.get_closest_marker("ghidra_native") is not None:
        ghidra, java = os.environ.get("RACP_TEST_GHIDRA"), os.environ.get("RACP_TEST_JAVA")
        if not ghidra or not java or not Path(ghidra).is_dir() or not Path(java).is_file():
            pytest.skip("requires explicit installed Ghidra and JDK executable paths")
        return PluginConfig(
            plugins=[
                create_ghidra_installation(
                    tmp_path / "ghidra-installation", Path(ghidra), Path(java)
                )
            ]
        )
    if request.node.get_closest_marker("gdb_native") is not None:
        gdb = os.environ.get("RACP_TEST_GDB")
        if not gdb or not Path(gdb).is_file():
            pytest.skip("requires explicit installed native GDB path")
        return PluginConfig(
            plugins=[
                create_gdb_installation(
                    tmp_path / "gdb-installation",
                    Path(gdb),
                    os.environ.get("RACP_TEST_GDB_VERSION", "17.1"),
                )
            ]
        )
    marker = request.node.get_closest_marker("re_plugin")
    if marker is None:
        return PluginConfig()
    root = tmp_path / "plugin-installation"
    root.mkdir()
    file = root / "manifest.json"
    script = Path(__file__).parent / "fixtures/re_plugin.py"
    operations = [
        name for name in RE_MODELS if not name.endswith(".backends") and name != "debugger.attach"
    ]
    manifest = PluginManifest.model_validate(
        {
            "name": "domain-fixture",
            "version": "1.0.0",
            "backend_name": "synthetic-contract",
            "backend_version": "fixture-1",
            "capabilities": ["static-analysis", "debugger"],
            "command": [
                sys.executable,
                "-I",
                str(script),
                "--manifest",
                str(file),
                "--mode",
                marker.kwargs.get("mode", "normal"),
            ],
            "working_directory": str(root),
            "required_permissions": ["re.read", "re.mutate", "debugger.read", "debugger.mutate"],
            "operations": [
                {
                    "name": name,
                    "capability": "static-analysis" if name.startswith("re.") else "debugger",
                    "permission_scope": name.split(".")[0]
                    + (".read" if name in RE_READS else ".mutate"),
                    "side_effect": name not in RE_READS,
                    "execution_modes": ["sync", "job"],
                    "input_schema": {"type": "object", **RE_MODELS[name].model_json_schema()},
                    "output_schema": {"type": "object"},
                    "max_timeout_ms": 86400000,
                }
                for name in operations
            ],
        }
    )
    file.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return PluginConfig(
        plugins=[
            PluginInstallation(
                manifest=file,
                sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                permissions=manifest.required_permissions,
            )
        ]
    )


def shell_request(
    live: dict[str, Any], argv: list[str], key: str = "test-key", **extra: Any
) -> dict[str, Any]:
    return {
        "device_id": live["device_id"],
        "operation": "shell.exec",
        "payload": {"argv": argv},
        "idempotency_key": key,
        "execution_profile_id": "trusted_personal",
        **extra,
    }
