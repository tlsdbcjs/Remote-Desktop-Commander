import asyncio
import json
import sys
from typing import Any

import httpx
import pytest
from racp_gateway.console_auth import COOKIE
from racp_gateway.loop import create_loop
from racp_sdk.security import digest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


@pytest.fixture
def _asyncio_loop_factory() -> Any:
    return create_loop


async def login(live: dict[str, Any], browser: httpx.AsyncClient) -> dict[str, Any]:
    setup = (await live["client"].post("/api/v1/console/setup-token")).json()
    response = await browser.post(
        "/api/v1/console/session", json={"setup_secret": setup["setup_secret"]}
    )
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "setup_secret" not in response.text and setup["setup_secret"] not in cookie
    consumed = await browser.post(
        "/api/v1/console/session", json={"setup_secret": setup["setup_secret"]}
    )
    assert consumed.status_code == 401
    return response.json()


async def next_event(lines: Any) -> dict[str, Any]:
    async with asyncio.timeout(5):
        async for line in lines:
            if line.startswith("data: "):
                return json.loads(line[6:])
    raise AssertionError("SSE stream ended before an event")


async def test_console_cookie_csrf_logout_and_device_separation(live: dict[str, Any]) -> None:
    async with httpx.AsyncClient(base_url=live["url"], headers={"Origin": live["url"]}) as browser:
        session = await login(live, browser)
        assert (await browser.get("/api/v1/devices")).status_code == 200
        assert (await browser.get("/api/v1/doctor")).json()["devices"][0]["id"] == live["device_id"]
        url = "/api/v1/enrollment-tokens"
        assert (await browser.post(url, json={"name": "no-csrf"})).status_code == 403
        assert (
            await browser.post(url, json={"name": "bad-csrf"}, headers={"X-CSRF-Token": "bad"})
        ).status_code == 403
        headers = {"X-CSRF-Token": session["csrf_token"]}
        assert (
            await browser.post(url, json={"name": "allowed"}, headers=headers)
        ).status_code == 200
        assert (
            await browser.post(
                url, json={"name": "origin"}, headers={**headers, "Origin": "https://attacker.test"}
            )
        ).status_code == 403
        assert (
            await browser.post(url, json={"name": "no-origin"}, headers={**headers, "Origin": ""})
        ).status_code == 403
        assert (await browser.post("/mcp/", json={})).status_code == 401
        assert (
            await browser.get(
                "/api/v1/devices", headers={"Authorization": "Bearer " + live["credential"]}
            )
        ).status_code == 401
        assert (await browser.delete("/api/v1/console/session", headers=headers)).status_code == 200
        assert (await browser.get("/api/v1/devices")).status_code == 401


async def test_console_idle_absolute_and_setup_expiry_are_monotonic(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = live["app"].state.console_auth
    clock = [1000.0]
    monkeypatch.setattr("racp_gateway.console_auth.monotonic", lambda: clock[0])
    async with httpx.AsyncClient(base_url=live["url"], headers={"Origin": live["url"]}) as browser:
        session = await login(live, browser)
        clock[0] += 1799
        assert (await browser.get("/api/v1/devices")).status_code == 200
        clock[0] += 1
        assert (await browser.get("/api/v1/devices")).status_code == 401
        session = await login(live, browser)
        # Read-only/background traffic never prolongs idle time.
        for _ in range(24):
            clock[0] += 1799
            result = await browser.post(
                "/api/v1/console/session/activity", headers={"X-CSRF-Token": session["csrf_token"]}
            )
            assert result.status_code == 200
        clock[0] += 24
        assert (await browser.get("/api/v1/devices")).status_code == 401
        setup = auth.setup("owner_local")
        clock[0] += 300
        assert (
            await browser.post(
                "/api/v1/console/session", json={"setup_secret": setup["setup_secret"]}
            )
        ).status_code == 401


async def test_console_https_cookie_and_cross_session_csrf(live: dict[str, Any]) -> None:
    async with httpx.AsyncClient(base_url=live["url"], headers={"Origin": live["url"]}) as first:
        first_session = await login(live, first)
        async with httpx.AsyncClient(
            base_url=live["url"], headers={"Origin": live["url"]}
        ) as second:
            await login(live, second)
            assert (
                await second.post(
                    "/api/v1/console/session/activity",
                    headers={"X-CSRF-Token": first_session["csrf_token"]},
                )
            ).status_code == 403
    setup = (await live["client"].post("/api/v1/console/setup-token")).json()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=live["app"]),
        base_url="https://testserver",
        headers={"Origin": "https://testserver"},
    ) as secure:
        response = await secure.post(
            "/api/v1/console/session", json={"setup_secret": setup["setup_secret"]}
        )
        assert response.status_code == 200
        assert "; secure" in response.headers["set-cookie"].lower()


async def test_sse_cookie_replay_gap_restart_and_logout_expiry(live: dict[str, Any]) -> None:
    async with httpx.AsyncClient(
        base_url=live["url"], headers={"Origin": live["url"]}, timeout=10
    ) as browser:
        session = await login(live, browser)
        async with browser.stream("GET", "/events") as response:
            assert response.status_code == 200
            lines = response.aiter_lines()
            ready = await next_event(lines)
            assert ready["type"] == "ready"
            feed = live["app"].state.events
            feed.store.device_status(live["device_id"], "DEGRADED")
            changed = await next_event(lines)
            assert changed["type"] == "change" and changed["device_id"] == live["device_id"]
            assert "credential" not in json.dumps(changed) and "payload" not in changed
        async with browser.stream(
            "GET", "/events", headers={"Last-Event-ID": ready["event_id"]}
        ) as response:
            lines = response.aiter_lines()
            await next_event(lines)
            assert (await next_event(lines))["event_id"] == changed["event_id"]
        await live["restart_gateway"]()
        async with browser.stream(
            "GET", "/events", headers={"Last-Event-ID": ready["event_id"]}
        ) as response:
            lines = response.aiter_lines()
            assert (await next_event(lines))["type"] == "ready"
            assert (await next_event(lines))["event_id"] == changed["event_id"]
        feed = live["app"].state.events
        feed.max_events = 2
        for _ in range(4):
            feed.store.audit("test_change", {"device_id": live["device_id"]})
        async with browser.stream(
            "GET", "/events", headers={"Last-Event-ID": ready["event_id"]}
        ) as response:
            lines = response.aiter_lines()
            gap = await next_event(lines)
            assert gap["type"] == "refresh" and gap["full_refresh"]
            cookie = browser.cookies.get(COOKIE)
            assert cookie and cookie != digest(cookie)
            await browser.delete(
                "/api/v1/console/session", headers={"X-CSRF-Token": session["csrf_token"]}
            )
            expired = await next_event(lines)
            assert expired["type"] == "session_expired"
            async for _ in lines:
                pass
        for _ in range(50):
            if not feed.active:
                break
            await asyncio.sleep(0.02)
        assert not feed.active


async def test_sse_and_session_ingress_reject_unauthorized_cross_origin_and_query_tokens(
    live: dict[str, Any],
) -> None:
    async with httpx.AsyncClient(base_url=live["url"]) as browser:
        assert (await browser.get("/events")).status_code == 401
        setup = (await live["client"].post("/api/v1/console/setup-token")).json()
        assert (
            await browser.post(
                "/api/v1/console/session", json={"setup_secret": setup["setup_secret"]}
            )
        ).status_code == 403
        assert (
            await browser.get("/events", headers={"Authorization": "Bearer " + live["credential"]})
        ).status_code == 401
    assert (
        await live["client"].get("/events?token=never", headers={"Origin": live["url"]})
    ).status_code == 400
    assert (
        await live["client"].get("/events", headers={"Last-Event-ID": "bad"})
    ).status_code == 400
    assert (
        await live["client"].get("/events", headers={"Origin": "https://attacker.test"})
    ).status_code == 403


async def test_cookie_terminal_stream_requires_origin_and_revocation_closes_quiet_stream(
    live: dict[str, Any],
) -> None:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "terminal.open",
            "payload": {"argv": [sys.executable, "-c", "import time; time.sleep(90)"]},
            "idempotency_key": "cookie-quiet-terminal",
            "execution_profile_id": "trusted_personal",
        },
    )
    handle = response.json()["result"]["handle_id"]
    url = (
        live["url"].replace("http://", "ws://")
        + f"/api/v1/devices/{live['device_id']}/terminals/{handle}/stream"
    )
    async with httpx.AsyncClient(base_url=live["url"], headers={"Origin": live["url"]}) as browser:
        session = await login(live, browser)
        cookie = browser.cookies.get(COOKIE)
        for origin in (None, "https://attacker.test"):
            with pytest.raises(InvalidStatus) as denied:
                async with connect(
                    url, additional_headers={"Cookie": f"{COOKIE}={cookie}"}, origin=origin
                ):
                    pass
            assert denied.value.response.status_code == 403
        async with connect(
            url, additional_headers={"Cookie": f"{COOKIE}={cookie}"}, origin=live["url"]
        ) as socket:
            await socket.send(json.dumps({"type": "stream_open", "cursor": "0"}))
            opened = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert opened["type"] == "stream_opened"
            await browser.delete(
                "/api/v1/console/session", headers={"X-CSRF-Token": session["csrf_token"]}
            )
            expired = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert (
                expired["type"] == "stream_error" and expired["error"]["code"] == "SESSION_EXPIRED"
            )
        for _ in range(100):
            if not live["app"].state.control.streams.relays:
                break
            await asyncio.sleep(0.02)
        assert not live["app"].state.control.streams.relays
