"""Chromium runs under the Rust Agent; Python supplies only the test Gateway/site."""

import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from .test_execution import execute, online


@pytest.fixture
def native_browser_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/auto":
                body = (
                    "<title>Auto</title><script>setTimeout(()=>{alert('private-dialog-text');"
                    "location.href='/changed'},300)</script>"
                )
            elif self.path == "/changed":
                body = (
                    "<title>Changed</title><script>"
                    "setTimeout(()=>window.open('/popup'),100)</script>"
                )
            elif self.path == "/popup":
                body = "<title>Popup</title><script>setTimeout(()=>window.close(),300)</script>"
            else:
                body = (
                    "<title>Rust native page</title><label>Name<input data-testid='name'></label>"
                    "<button onclick=\"document.querySelector('p').textContent='Hello '+"
                    "document.querySelector('input').value\">Submit</button><p></p>"
                )
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
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


@pytest.mark.asyncio
async def test_browser_native_input_artifact_and_passive_gateway_events(
    rust_live, native_browser_site
):
    live = rust_live
    await online(live)
    opened = await execute(live, "browser.open", {})
    assert opened["state"] == "SUCCEEDED", opened
    assert await execute(live, "browser.open", {}) == opened
    target = {
        "browser_id": opened["result"]["browser_id"],
        "page_id": opened["result"]["pages"][0]["page_id"],
    }
    navigated = await execute(live, "browser.navigate", {**target, "url": native_browser_site})
    assert navigated["state"] == "SUCCEEDED", navigated
    snapshot = await execute(live, "browser.snapshot", target)
    assert snapshot["state"] == "SUCCEEDED", snapshot
    info = snapshot["result"]
    assert info["title"] == "Rust native page" and info["trust"] == "untrusted_page_data"
    observed = {
        **target,
        "observation_id": info["observation_id"],
        "navigation_revision": info["navigation_revision"],
    }
    typed = await execute(
        live,
        "browser.type",
        {**observed, "selector": {"by": "test_id", "value": "name"}, "text": "Rust 한글 🙂"},
    )
    assert typed["state"] == "SUCCEEDED", typed
    clicked = await execute(
        live,
        "browser.click",
        {**observed, "selector": {"by": "role", "value": "button", "name": "Submit"}},
    )
    assert clicked["state"] == "SUCCEEDED", clicked
    image = await execute(live, "browser.screenshot", target)
    assert image["state"] == "SUCCEEDED", image
    for _ in range(100):
        outputs = (
            await live["http"].get("/api/v1/operations/" + image["operation_id"] + "/outputs")
        ).json()
        output = next(
            item for item in outputs["items"] if item["id"] == image["result"]["output_id"]
        )
        if output.get("artifact_id"):
            break
        await asyncio.sleep(0.05)
    assert output["artifact_id"], output
    content = await live["http"].get("/api/v1/artifacts/" + output["artifact_id"] + "/content")
    assert content.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(content.content).hexdigest() == image["result"]["sha256"]
    assert output["media_type"] == "image/png"
    changed = await execute(
        live, "browser.navigate", {**target, "url": native_browser_site + "/auto"}, key="auto-nav"
    )
    assert changed["state"] == "SUCCEEDED", changed
    for _ in range(200):
        handles = (
            await live["http"].get("/api/v1/devices/" + live["device_id"] + "/handles")
        ).json()["items"]
        popups = [
            h for h in handles if h["type"] == "browser-page" and h["id"] != target["page_id"]
        ]
        if popups and popups[0]["state"] == "CLOSED":
            break
        await asyncio.sleep(0.025)
    assert popups and popups[0]["state"] == "CLOSED", handles
    audit = (
        await live["http"].get("/api/v1/audit", params={"event": "browser_state_changed"})
    ).json()["items"]
    assert any(row["summary"]["event_kind"] == "dialog" for row in audit), audit
    assert "private-dialog-text" not in json.dumps(audit)
    assert native_browser_site not in json.dumps(audit)
    sequences = [row["summary"]["event_sequence"] for row in audit]
    assert len(sequences) == len(set(sequences))
    closed = await execute(live, "browser.close", {"browser_id": target["browser_id"]})
    assert closed["state"] == "SUCCEEDED", closed
    assert closed["result"]["cleanup_status"] == "complete"
