import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from test_browser import browser_site as browser_site
from test_browser import succeeded


@pytest.fixture
def event_site(browser_site: str) -> Any:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/auto":
                html = """<title>Auto</title><script>
                setTimeout(()=>{alert('secret-dialog-must-not-leak');location.href='/changed';},250);
                </script>"""
            elif self.path == "/changed":
                html = """<title>Changed</title><script>
                setTimeout(()=>window.open('/popup'),100);</script>"""
            else:
                html = (
                    """<title>Popup</title><script>setTimeout(()=>window.close(),300);</script>"""
                )
            raw = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

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


async def test_async_browser_events_update_gateway_handles_without_browser_poll_and_replay_once(
    live: dict[str, Any], event_site: str
) -> None:
    opened = await succeeded(live, "open", {})
    target = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": event_site + "/auto"})
    # Poll only Gateway snapshots: no further worker/browser operation advances events.
    for _ in range(100):
        handles = (
            await live["client"].get("/api/v1/devices/" + live["device_id"] + "/handles")
        ).json()["items"]
        popups = [
            h for h in handles if h["type"] == "browser-page" and h["id"] != target["page_id"]
        ]
        if popups and popups[0]["state"] == "CLOSED":
            break
        await asyncio.sleep(0.025)
    assert popups and popups[0]["state"] == "CLOSED"
    audit = (
        await live["client"].get("/api/v1/audit", params={"event": "browser_state_changed"})
    ).json()["items"]
    assert any(row["summary"]["event_kind"] == "dialog" for row in audit)
    assert any(row["summary"]["event_kind"] == "navigation" for row in audit)
    assert "secret-dialog-must-not-leak" not in json.dumps(audit)
    assert event_site not in json.dumps(audit)
    assert live["agent"].lease_expires > asyncio.get_running_loop().time()
    await live["restart_gateway"]()
    for _ in range(100):
        if not live["agent"].browser_events.pending:
            break
        await asyncio.sleep(0.025)
    assert not live["agent"].browser_events.pending
    store = live["app"].state.control.store
    sequences = [
        json.loads(row[0])["event_sequence"]
        for row in store.db.execute("SELECT summary FROM audit WHERE event='browser_state_changed'")
    ]
    assert len(sequences) == len(set(sequences))


async def test_missing_native_browser_is_unavailable_while_filesystem_core_stays_online(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(live["workspace"] / "missing-cache"))
    capability = live["agent"].browser_capability()
    assert not capability.healthy and "browser.open" not in capability.operations
    live["agent"].browser_events.push(
        {"kind": "health", "state": "unavailable", "capability": capability.model_dump()}, []
    )
    for _ in range(100):
        device = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
        browser = next(c for c in device["info"]["capabilities"] if c["name"] == "browser")
        if not browser["healthy"]:
            break
        await asyncio.sleep(0.025)
    assert not browser["healthy"] and device["info"]["status"] == "ONLINE"
    doctor = (await live["client"].get("/api/v1/doctor")).json()
    assert doctor["status"] == "degraded"
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "filesystem.stat",
            "payload": {"path": "."},
        },
    )
    assert response.json()["state"] == "SUCCEEDED"
