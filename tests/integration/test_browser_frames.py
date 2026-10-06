import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from test_browser import browser_site as browser_site
from test_browser import call, observed, succeeded


@pytest.fixture
def frame_site(browser_site: str) -> Any:
    cross_url = ""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/root"):
                html = f"""<!doctype html><title>Main</title>
                <label>Same field<input data-testid="field"></label>
                <button data-testid="submit" onclick="
                    document.querySelector('#out').textContent='main';">Submit</button>
                <p id="out"></p><iframe name="same" src="/child"></iframe>
                <iframe name="cross" src="{cross_url}/child"></iframe>"""
            else:
                html = """<!doctype html><title>Child</title>
                <label>Same field<input data-testid="field"></label>
                <button data-testid="submit" onclick="document.querySelector('#out').textContent=
                    'child:'+document.querySelector('input').value;">Submit</button>
                <p id="out"></p>"""
            raw = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    cross_url = f"http://127.0.0.1:{servers[1].server_port}"
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    try:
        yield f"http://127.0.0.1:{servers[0].server_port}"
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(2)


async def setup_page(live: dict[str, Any], url: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    opened = await succeeded(live, "open", {})
    target = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": url + "/root"})
    for _ in range(100):
        frames = (await succeeded(live, "frames", target))["frames"]
        if len(frames) == 3 and all(f["url"].startswith("http") for f in frames):
            return target, list(frames)
        await asyncio.sleep(0.02)
    raise AssertionError("child frames did not load")


async def test_browser_frame_scope_cross_origin_form_and_key_target(
    live: dict[str, Any], frame_site: str
) -> None:
    target, frames = await setup_page(live, frame_site)
    child = next(f for f in frames if f["name"] == "cross")
    scoped = {**target, "frame_id": child["frame_id"]}
    snapshot = await succeeded(live, "snapshot", scoped)
    assert snapshot["title"] == "Child" and snapshot["frame_id"] == child["frame_id"]
    assert snapshot["url"] != snapshot["page_url"]
    ref = next(e["ref"] for e in snapshot["elements"] if e["test_id"] == "field")
    await succeeded(live, "type", {**observed(scoped, snapshot), "ref": ref, "text": "frame"})
    await succeeded(live, "key", {**observed(scoped, snapshot), "key": "End"})
    wrong = await call(live, "type", {**observed(target, snapshot), "ref": ref, "text": "wrong"})
    assert wrong["error"]["code"] == "STALE_OBSERVATION"
    await succeeded(
        live,
        "click",
        {**observed(scoped, snapshot), "selector": {"by": "test_id", "value": "submit"}},
    )
    snapshot = await succeeded(live, "snapshot", scoped)
    assert "child:frame" in snapshot["semantic_tree"]
    main = await succeeded(live, "snapshot", target)
    assert "child:frame" not in main["semantic_tree"]
    # The page's focus is inside the child; a main-frame key must not leak there.
    leaked = await call(live, "key", {**observed(target, main), "key": "Tab"})
    assert leaked["error"]["code"] == "FOCUS_MISMATCH"
    another, _ = await setup_page(live, frame_site)
    wrong_page = await call(live, "snapshot", {**another, "frame_id": child["frame_id"]})
    assert wrong_page["error"]["code"] == "STALE_OBSERVATION"


async def test_frame_navigation_detachment_and_paging_never_reuses_old_observations(
    live: dict[str, Any], frame_site: str
) -> None:
    target, frames = await setup_page(live, frame_site)
    first = await succeeded(live, "frames", {**target, "limit": 1})
    second = await succeeded(live, "frames", {**target, "limit": 1, "cursor": first["next_cursor"]})
    assert first["frames"][0]["frame_id"] != second["frames"][0]["frame_id"]
    child = next(f for f in frames if f["name"] == "same")
    scoped = {**target, "frame_id": child["frame_id"]}
    snapshot = await succeeded(live, "snapshot", scoped)
    precondition = observed(scoped, snapshot)
    await succeeded(live, "navigate", {**scoped, "url": frame_site + "/child?new"})
    stale = await call(
        live, "click", {**precondition, "selector": {"by": "test_id", "value": "submit"}}
    )
    assert stale["error"]["code"] == "STALE_OBSERVATION"
    main = await succeeded(live, "snapshot", target)
    await succeeded(
        live,
        "evaluate",
        {
            **observed(target, main),
            "expression": "() => {document.querySelector('iframe[name=same]').remove();"
            " return true;}",
        },
    )
    removed = await call(live, "snapshot", scoped)
    assert removed["error"]["code"] == "STALE_OBSERVATION"
    foreign, _ = await setup_page(live, frame_site)
    wrong_cursor = await call(
        live, "frames", {**foreign, "limit": 1, "cursor": first["next_cursor"]}
    )
    assert wrong_cursor["error"]["code"] == "STALE_OBSERVATION"
