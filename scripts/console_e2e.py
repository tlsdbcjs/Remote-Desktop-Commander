"""Run the production Console against an isolated real Gateway and Agent process."""

import argparse
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil
import uvicorn
from fastapi import Request
from racp_gateway.app import bearer, create_app
from racp_gateway.loop import create_loop
from racp_gateway.store import GatewayStore
from racp_sdk.security import SecretStore, digest, token


async def run(node: Path, grep: str | None = None) -> int:
    browser_path = await asyncio.to_thread(Path(".tools/playwright").resolve)
    playwright_cli = await asyncio.to_thread(
        Path("apps/console/node_modules/@playwright/test/cli.js").resolve
    )
    with tempfile.TemporaryDirectory(prefix="racp-console-") as temporary, ExitStack() as resources:
        root = Path(temporary)
        workspace = root / "한글 workspace"
        workspace.mkdir()
        documents = root / "두 번째 자료"
        documents.mkdir()
        owner = token()
        store = GatewayStore(root / "gateway/gateway.db")
        store.initialize(digest(owner))
        enrolled = store.enroll(store.enrollment("Console fixture"))
        store.close()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        credentials = root / "agent.bin"
        SecretStore(credentials).save({**enrolled, "gateway": url})
        app = create_app(root / "gateway", trusted_personal=True)
        agent_log = resources.enter_context((root / "agent.log").open("wb"))
        desktop_args: list[str] = []
        if os.name == "nt":
            from racp_agent.broker.identity import process_identity

            session_id = process_identity(os.getpid()).session
            if session_id > 0:
                desktop_args = ["--desktop-session-id", str(session_id)]

        def start_agent(browser_cache: Path | None = None) -> subprocess.Popen[bytes]:
            environment = dict(os.environ)
            if browser_cache is not None:
                environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "racp_agent.main",
                    "--credentials",
                    str(credentials),
                    "--data-dir",
                    str(root / "agent"),
                    "--workspace",
                    str(workspace),
                    "--allow-workspace",
                    "docs=" + str(documents),
                    "--profile",
                    "trusted_personal",
                    *desktop_args,
                ],
                stdout=agent_log,
                stderr=subprocess.STDOUT,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        agent = start_agent()

        def stop_agent() -> None:
            if agent.poll() is not None:
                return
            owned = [
                (child.pid, child.create_time())
                for child in psutil.Process(agent.pid).children(recursive=True)
            ]
            agent.terminate()
            agent.wait(5)
            # The crash fixture owns these exact children. POSIX process groups do
            # not offer Windows Job's kill-on-parent-close guarantee.
            for pid, created in owned:
                try:
                    child = psutil.Process(pid)
                    if child.create_time() == created:
                        child.kill()
                except psutil.NoSuchProcess:
                    pass

        @app.post("/fixture/stop-agent")
        async def stop_fixture_agent(request: Request) -> dict[str, str]:
            app.state.control.store.owner(bearer(request))
            await asyncio.to_thread(stop_agent)
            return {"state": "stopped"}

        @app.post("/fixture/approval-expire")
        async def expire_fixture_approval(request: Request) -> dict[str, str]:
            principal = app.state.control.store.owner(bearer(request))
            value = await request.json()
            record = app.state.control.store.approval_record(value["id"])
            operation = app.state.control.store.get(record["operation_id"])
            app.state.control.store.device(operation["device_id"], principal)
            app.state.control.store.db.execute(
                "UPDATE approvals SET expires=? WHERE id=?", (time.time() - 1, value["id"])
            )
            app.state.control.store.audit("fixture_approval_expired", operation["request"])
            return {"state": "expired"}

        @app.post("/fixture/event-gap")
        async def fixture_event_gap(request: Request) -> dict[str, str]:
            principal = app.state.control.store.owner(bearer(request))
            feed = app.state.events
            previous = feed.max_events
            try:
                feed.max_events = 2
                for _ in range(8):
                    feed.publish(principal, "fixture_gap", {"device_id": enrolled["device_id"]})
            finally:
                feed.max_events = previous
            return {"state": "gap"}

        @app.post("/fixture/expire-outcomes")
        async def fixture_expire_outcomes(request: Request) -> dict[str, Any]:
            app.state.control.store.owner(bearer(request))
            value = app.state.control.store.compact(now=datetime.now(UTC) + timedelta(days=2))
            app.state.control.store.audit("fixture_outcomes_expired", {})
            return value

        async def restart_agent(
            request: Request, browser_cache: Path | None = None
        ) -> dict[str, str]:
            # Test-only route, never installed by create_app or included in wheels.
            nonlocal agent
            app.state.control.store.owner(bearer(request))
            previous_epoch = app.state.control.store.device(enrolled["device_id"])["epoch"]
            await asyncio.to_thread(stop_agent)
            agent = start_agent(browser_cache)
            # OS process/Broker startup precedes the UI event-delivery assertion.
            # Observe real reconciliation; never synthesize ONLINE or UNKNOWN.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                connection = app.state.control.connections.get(enrolled["device_id"])
                if connection and connection.ready and connection.epoch > previous_epoch:
                    return {"state": "connected"}
                if agent.poll() is not None:
                    raise RuntimeError("restarted fixture Agent exited before reconciliation")
                await asyncio.sleep(0.05)
            raise RuntimeError("restarted fixture Agent did not reconcile within 30 seconds")

        @app.post("/fixture/restart-agent")
        async def restart_fixture(request: Request) -> dict[str, str]:
            return await restart_agent(request)

        @app.post("/fixture/degraded-agent")
        async def degraded_fixture(request: Request) -> dict[str, str]:
            return await restart_agent(request, root / "missing-browser-cache")

        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", ws_per_message_deflate=False)
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(200):
                if (
                    server.started
                    and app.state.control.store.device(enrolled["device_id"])["info"].get("status")
                    == "ONLINE"
                ):
                    break
                if agent.poll() is not None:
                    raise RuntimeError("fixture Agent exited during startup")
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("fixture Gateway/Agent did not start")
            env = dict(os.environ)
            env.update(
                {
                    "RACP_BASE_URL": url,
                    "RACP_OWNER_TOKEN": owner,
                    "RACP_DEVICE_ID": enrolled["device_id"],
                    "RACP_TEST_PYTHON": sys.executable,
                    "RACP_TEST_WORKSPACE": str(workspace.resolve()),
                    "RACP_TEST_SECOND_WORKSPACE": str(documents.resolve()),
                    "RACP_DESKTOP_SESSION_ID": desktop_args[-1] if desktop_args else "",
                    "PLAYWRIGHT_BROWSERS_PATH": str(browser_path),
                    "PLAYWRIGHT_HTML_OPEN": "never",
                }
            )
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    str(node),
                    str(playwright_cli),
                    "test",
                    "console.spec.ts",
                    *(["--grep", grep] if grep else []),
                ],
                cwd=Path("apps/console"),
                env=env,
                check=False,
            )
            if result.returncode:
                agent_log.flush()
                shutil.copyfile(root / "agent.log", Path("dist/console-agent-failure.log"))
            return result.returncode
        finally:
            if agent.poll() is None:
                await asyncio.to_thread(stop_agent)
            server.should_exit = True
            await asyncio.wait_for(server_task, 10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", type=Path, default=Path(".tools/node-v22.23.0-win-x64/node.exe"))
    parser.add_argument("--grep", help="Run a specific acceptance case during failure diagnosis")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.node.resolve(), args.grep), loop_factory=create_loop))


if __name__ == "__main__":
    main()
