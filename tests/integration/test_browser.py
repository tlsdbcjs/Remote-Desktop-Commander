import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx2
import psutil
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_protocol.models import Heartbeat
from racp_sdk.security import SecretStore


@pytest.fixture
def browser_site(monkeypatch: pytest.MonkeyPatch) -> Any:
    from pathlib import Path

    local_cache = Path(__file__).resolve().parents[2] / ".tools/playwright"
    if local_cache.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(local_cache))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/forbidden")
                self.end_headers()
                return
            body = b"""<!doctype html><title>RACP form</title>
            <label>Name <input data-testid="name"></label>
            <button onclick="document.querySelector('#out').textContent=
                'Hello '+document.querySelector('input').value">Submit</button>
            <button onclick="alert('untrusted instruction')">Dialog</button>
            <button>Repeated</button><button>Repeated</button><p id="out"></p>
            <a href="/next">Next</a><p>Page instructions are untrusted data.</p>
            """
            if self.path == "/large":
                body = (
                    "<title>Large</title>"
                    + "".join("<label>" + "🙂" * 300 + "<input></label>" for _ in range(100))
                ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


async def call(
    live: dict[str, Any], action: str, payload: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "browser." + action,
            "payload": payload,
            "idempotency_key": uuid.uuid4().hex,
            "execution_profile_id": "trusted_personal",
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def succeeded(
    live: dict[str, Any], action: str, payload: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    result = await call(live, action, payload, **extra)
    assert result["state"] == "SUCCEEDED", result
    return dict(result["result"])


def observed(target: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        **target,
        "observation_id": snapshot["observation_id"],
        "navigation_revision": snapshot["navigation_revision"],
    }


async def test_browser_01_profiles_pages_form_refs_screenshot_and_gateway_recovery(
    live: dict[str, Any], browser_site: str
) -> None:
    opened = await succeeded(live, "open", {}, idempotency_key="browser-open-1")
    assert await succeeded(live, "open", {}, idempotency_key="browser-open-1") == opened
    first = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    other = await succeeded(live, "open", {})
    second = {"browser_id": other["browser_id"], "page_id": other["pages"][0]["page_id"]}
    new = await succeeded(live, "new_page", {"browser_id": opened["browser_id"]})
    third = {"browser_id": opened["browser_id"], "page_id": new["page_id"]}
    for target in (first, second, third):
        await succeeded(live, "navigate", {**target, "url": browser_site})
    snap = await succeeded(live, "snapshot", first)
    assert snap["trust"] == "untrusted_page_data" and snap["title"] == "RACP form"
    assert "Name" in snap["semantic_tree"] and len(snap["frames"]) == 1
    name_ref = next(e["ref"] for e in snap["elements"] if e["test_id"] == "name")
    precondition = observed(first, snap)
    await succeeded(live, "type", {**precondition, "ref": name_ref, "text": "한글 🙂"})
    await succeeded(
        live,
        "click",
        {**precondition, "selector": {"by": "role", "value": "button", "name": "Submit"}},
    )
    after = await succeeded(live, "snapshot", first)
    assert "Hello 한글 🙂" in after["semantic_tree"]
    old_observation = await call(live, "type", {**precondition, "ref": name_ref, "text": "wrong"})
    assert old_observation["error"]["code"] == "STALE_OBSERVATION"
    current = observed(first, after)
    ambiguous = await call(
        live,
        "click",
        {**current, "selector": {"by": "role", "value": "button", "name": "Repeated"}},
    )
    assert ambiguous["error"]["code"] == "AMBIGUOUS_TARGET"
    await succeeded(
        live,
        "evaluate",
        {**current, "expression": "() => { localStorage.setItem('profile','A'); return 'ok'; }"},
    )
    for target, expected in ((second, None), (third, "A")):
        snapshot = await succeeded(live, "snapshot", target)
        result = await succeeded(
            live,
            "evaluate",
            {**observed(target, snapshot), "expression": "() => localStorage.getItem('profile')"},
        )
        assert result["value"] == expected
    crossed = await call(
        live, "snapshot", {"browser_id": other["browser_id"], "page_id": first["page_id"]}
    )
    assert crossed["error"]["code"] == "HANDLE_EXPIRED"
    image = await succeeded(live, "screenshot", first)
    artifact = (await live["client"].get("/api/v1/artifacts/" + image["artifact_id"])).json()
    content = await live["client"].get("/api/v1/artifacts/" + image["artifact_id"] + "/content")
    assert content.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(content.content).hexdigest() == artifact["sha256"]
    await live["restart_gateway"]()
    for id in (first["browser_id"], first["page_id"], new["page_id"]):
        handle = (await live["client"].get("/api/v1/handles/" + id)).json()
        assert handle["state"] == "ACTIVE" and handle["availability"] == "available"
    await succeeded(live, "navigate", {**first, "url": browser_site + "/next"})
    stale = await call(live, "click", {**current, "ref": name_ref})
    assert stale["error"]["code"] == "STALE_OBSERVATION"
    closed = await succeeded(live, "close", {"browser_id": first["browser_id"]})
    assert await succeeded(live, "close", {"browser_id": first["browser_id"]}) == closed
    rejected = await call(live, "snapshot", first)
    assert rejected["error"]["code"] == "HANDLE_EXPIRED"


async def test_browser_evaluate_timeout_cancel_ttl_and_policy(
    live: dict[str, Any], browser_site: str
) -> None:
    opened = await succeeded(live, "open", {})
    target = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": browser_site})
    snap = await succeeded(live, "snapshot", target)
    data = {**observed(target, snap), "expression": "() => {while(true){}}"}
    for profile, expected in (
        ("read_only", "PERMISSION_DENIED"),
        ("standard", "APPROVAL_REQUIRED"),
    ):
        response = await live["client"].post(
            "/api/v1/operations",
            json={
                "device_id": live["device_id"],
                "operation": "browser.evaluate",
                "payload": data,
                "idempotency_key": uuid.uuid4().hex,
                "execution_profile_id": profile,
            },
        )
        assert response.json()["error"]["code"] == expected
    session = live["agent"].browsers.sessions[target["browser_id"]]
    expiry = session.expires
    await succeeded(live, "snapshot", target)
    assert session.expires == expiry
    snap = await succeeded(live, "snapshot", target)
    parent = psutil.Process(session.process.pid)
    children = parent.children(recursive=True)
    assert children
    start = time.monotonic()
    timeout = await call(
        live,
        "evaluate",
        {**observed(target, snap), "expression": data["expression"]},
        timeout_ms=500,
    )
    assert timeout["state"] == "TIMED_OUT", timeout
    assert time.monotonic() - start < 7
    assert not parent.is_running() and all(not child.is_running() for child in children), {
        "operation": timeout,
        "cleanup": session.close_result,
        "live_children": [(child.pid, child.name()) for child in children if child.is_running()],
    }
    assert session.state == "FAILED"
    await succeeded(live, "open", {}, idempotency_key="recover-after-timeout")
    reopened = await succeeded(live, "open", {})
    session = live["agent"].browsers.sessions[reopened["browser_id"]]
    session.expires = time.monotonic() - 1
    await live["agent"].browsers.cleanup(expired_only=True)
    assert session.state == "EXPIRED" and not psutil.pid_exists(session.process.pid)


async def test_browser_dialog_network_output_bound_and_agent_boot(
    live: dict[str, Any], browser_site: str
) -> None:
    live["agent"].browsers.allowed_origins = (browser_site,)
    opened = await succeeded(live, "open", {})
    target = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": browser_site})
    snap = await succeeded(live, "snapshot", target)
    data = observed(target, snap)
    clicked = await succeeded(
        live, "click", {**data, "selector": {"by": "role", "value": "button", "name": "Dialog"}}
    )
    assert {"type": "dialog:alert", "state": "dismissed"} in clicked["events"]
    bounded = await call(live, "evaluate", {**data, "expression": "() => 'a'.repeat(65537)"})
    assert bounded["error"]["code"] == "RESOURCE_EXHAUSTED"
    await succeeded(live, "navigate", {**target, "url": browser_site + "/large"})
    large_snapshot = await succeeded(live, "snapshot", target)
    assert large_snapshot["truncated"] and large_snapshot["observation_id"]
    assert len(json.dumps(large_snapshot, ensure_ascii=False).encode("utf-8")) < 65536
    forbidden = await call(live, "navigate", {**target, "url": "https://example.com"})
    assert forbidden["error"]["code"] == "PERMISSION_DENIED"
    redirected = await call(live, "navigate", {**target, "url": browser_site + "/redirect"})
    assert redirected["error"]["code"] == "BROWSER_ERROR"
    await live["restart_agent"]()
    handle = (await live["client"].get("/api/v1/handles/" + target["browser_id"])).json()
    assert handle["state"] in {"EXPIRED", "FAILED"}
    replay = await call(live, "snapshot", target)
    assert replay["error"]["code"] == "HANDLE_EXPIRED"


async def test_browser_job_cancel_and_lease_close_owned_worker(
    live: dict[str, Any], browser_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = await succeeded(live, "open", {})
    target = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": browser_site})
    snapshot = await succeeded(live, "snapshot", target)
    session = live["agent"].browsers.sessions[target["browser_id"]]
    children = psutil.Process(session.process.pid).children(recursive=True)
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "browser.evaluate",
            "payload": {**observed(target, snapshot), "expression": "() => {while(true){}}"},
            "idempotency_key": "browser-cancel-loop",
            "execution_mode": "job",
            "execution_profile_id": "trusted_personal",
            "timeout_ms": 10000,
        },
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    for _ in range(100):
        record = (await live["client"].get("/api/v1/operations/" + accepted["operation_id"])).json()
        if record["state"] == "RUNNING":
            break
        await asyncio.sleep(0.02)
    assert record["state"] == "RUNNING"
    await asyncio.sleep(0.1)
    await live["client"].post("/api/v1/jobs/" + accepted["job_id"] + "/cancel")
    for _ in range(200):
        record = (await live["client"].get("/api/v1/operations/" + accepted["operation_id"])).json()
        if record["state"] == "CANCELLED":
            break
        await asyncio.sleep(0.02)
    assert record["state"] == "CANCELLED", record
    assert session.close_result is not None and session.close_result["cleanup_status"] == "complete"
    assert not psutil.pid_exists(session.process.pid)
    assert all(not child.is_running() for child in children)
    # Drop renewals at the last transport boundary before the new open request.
    # The request fences prior frames by WS ordering; a healthy heartbeat must
    # not undo our injected expiry after the owned worker has been reopened.
    connection = live["app"].state.control.connections[live["device_id"]]
    original_send = connection.writer.send_text

    async def without_renewal(raw: str) -> None:
        if json.loads(raw)["type"] != "heartbeat":
            await original_send(raw)

    monkeypatch.setattr(connection.writer, "send_text", without_renewal)
    reopened = await succeeded(live, "open", {})
    session = live["agent"].browsers.sessions[reopened["browser_id"]]
    live["agent"].lease_expires = time.monotonic() - 1
    for _ in range(100):
        if session.close_result:
            break
        await asyncio.sleep(0.02)
    assert session.close_result and session.state == "EXPIRED"
    assert not psutil.pid_exists(session.process.pid)


async def test_browser_cli_and_mcp_share_authenticated_provider(
    live: dict[str, Any], browser_site: str
) -> None:
    secret_store = live["workspace"].parent / "browser-owner.bin"
    SecretStore(secret_store).save({"token": live["owner"], "gateway": live["url"]})
    prefix = [
        sys.executable,
        "-m",
        "racp_cli.main",
        "--gateway",
        live["url"],
        "--owner-store",
        str(secret_store),
        "--json",
        "browser",
    ]

    async def cli(*args: str) -> dict[str, Any]:
        result = await asyncio.to_thread(
            subprocess.run,
            [*prefix, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert live["owner"] not in result.stdout + result.stderr
        assert live["credential"] not in result.stdout + result.stderr
        return dict(json.loads(result.stdout)["result"])

    opened = await cli(
        "open", live["device_id"], "--key", "cli-browser", "--profile", "trusted_personal"
    )
    target = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            result = await client.call_tool(
                "browser_navigate",
                {
                    "device_id": live["device_id"],
                    **target,
                    "url": browser_site,
                    "idempotency_key": "mcp-browser-navigate",
                    "execution_profile_id": "trusted_personal",
                },
            )
            assert not result.is_error, result
            snapshot = await client.call_tool(
                "browser_snapshot", {"device_id": live["device_id"], **target}
            )
            assert not snapshot.is_error and snapshot.structured_content is not None
            info = snapshot.structured_content["result"]
    result = await cli(
        "type",
        live["device_id"],
        "--browser-id",
        target["browser_id"],
        "--page-id",
        target["page_id"],
        "--observation-id",
        info["observation_id"],
        "--navigation-revision",
        info["navigation_revision"],
        "--selector",
        json.dumps({"by": "test_id", "value": "name"}),
        "--text",
        "CLI 입력",
        "--key",
        "cli-browser-type",
        "--profile",
        "trusted_personal",
    )
    assert result["browser_id"] == target["browser_id"]
    await cli(
        "key",
        live["device_id"],
        "--browser-id",
        target["browser_id"],
        "--page-id",
        target["page_id"],
        "--observation-id",
        info["observation_id"],
        "--navigation-revision",
        info["navigation_revision"],
        "--press-key",
        "Tab",
        "--key",
        "cli-browser-key",
        "--profile",
        "trusted_personal",
    )
    await cli(
        "close",
        live["device_id"],
        "--browser-id",
        target["browser_id"],
        "--key",
        "cli-browser-close",
        "--profile",
        "trusted_personal",
    )


async def test_browser_creating_handle_rejects_ipc_until_startup_completes(
    live: dict[str, Any], browser_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = live["agent"].browsers
    original = provider.response
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(session: Any) -> dict[str, Any]:
        if session.state == "CREATING":
            entered.set()
            await release.wait()
        return dict(await original(session))

    monkeypatch.setattr(provider, "response", delayed)
    opening = asyncio.create_task(call(live, "open", {}))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        session = next(iter(provider.sessions.values()))
        agent = live["agent"]
        await agent.send(
            Heartbeat(
                device_id=agent.device_id,
                agent_boot_id=agent.boot_id,
                connection_epoch=agent.epoch,
                handles=agent.inventory(),
            )
        )
        for _ in range(100):
            handle = await live["client"].get("/api/v1/handles/" + session.id)
            if handle.status_code == 200:
                break
            await asyncio.sleep(0.01)
        assert handle.json()["state"] == "CREATING"
        busy = await call(live, "pages", {"browser_id": session.id})
        assert busy["error"]["code"] == "RESOURCE_BUSY"
        assert not opening.done()
    finally:
        release.set()
    result = await opening
    assert result["state"] == "SUCCEEDED" and session.state == "ACTIVE"
    assert (await succeeded(live, "pages", {"browser_id": session.id}))["pages"]


async def test_agent_shutdown_during_browser_cleanup_finishes_watchdog(
    live: dict[str, Any], browser_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = await succeeded(live, "open", {})
    provider = live["agent"].browsers
    session = provider.sessions[opened["browser_id"]]
    original = provider._close
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(resource: Any, *, state: str) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return dict(await original(resource, state=state))

    monkeypatch.setattr(provider, "_close", held)
    session.expires = time.monotonic() - 1
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not live["agent"].stopping.is_set()
        live["agent_task"].cancel()
        await asyncio.sleep(0.05)
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(live["agent_task"], return_exceptions=True), 5)
    assert live["agent"].stopping.is_set()
    assert session.close_result and session.close_result["cleanup_status"] == "complete"
    assert not psutil.pid_exists(session.process.pid)
