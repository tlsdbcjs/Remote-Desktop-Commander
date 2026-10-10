"""Chromium runs under the Rust Agent; Python supplies only the test Gateway/site."""

import asyncio
import hashlib
import json
import os
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
            raw = body.encode()
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


@pytest.fixture
def native_file_site():
    raw = bytes(range(256)) * 4096
    counts = {"downloads": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in {"/file", "/large"}:
                if "native-session=private" not in self.headers.get("Cookie", ""):
                    self.send_error(403)
                    return
                counts["downloads"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="../../outside.bin"')
                self.send_header(
                    "Content-Length", str(len(raw) * (100 if self.path == "/large" else 1))
                )
                self.end_headers()
                try:
                    for _ in range(100 if self.path == "/large" else 1):
                        self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            body = (
                "<title>Native files</title><input type='file' data-testid='upload'>"
                "<a data-testid='download' href='/file'>Download</a>"
                "<a data-testid='large' href='/large'>Large</a>"
                "<button data-testid='blob' onclick=\"const a=document.createElement('a');"
                "a.href=window.URL.createObjectURL(new Blob(['native blob 한글']));"
                "a.download='blob.bin';a.click()\">Blob</button>"
            ).encode()
            self.send_response(200)
            self.send_header("Set-Cookie", "native-session=private; HttpOnly; Path=/")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{server.server_port}", "raw": raw, "counts": counts}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


async def verified_output(live, outcome):
    for _ in range(600):
        outputs = (
            await live["http"].get("/api/v1/operations/" + outcome["operation_id"] + "/outputs")
        ).json()["items"]
        output = next(row for row in outputs if row["id"] == outcome["result"]["output_id"])
        if output["artifact_id"]:
            break
        await asyncio.sleep(0.05)
    assert output["artifact_id"], output
    content = await live["http"].get("/api/v1/artifacts/" + output["artifact_id"] + "/content")
    assert hashlib.sha256(content.content).hexdigest() == output["sha256"]
    return content.content


@pytest.mark.asyncio
async def test_native_cookie_blob_download_and_retained_artifact_upload(
    rust_live, native_file_site
):
    from racp_sdk.artifacts import ArtifactClient

    live, site = rust_live, native_file_site
    await online(live)
    opened = await execute(live, "browser.open", {})
    assert opened["state"] == "SUCCEEDED", opened
    target = {
        "browser_id": opened["result"]["browser_id"],
        "page_id": opened["result"]["pages"][0]["page_id"],
    }
    navigated = await execute(live, "browser.navigate", {**target, "url": site["url"]})
    assert navigated["state"] == "SUCCEEDED", navigated
    snapshot = await execute(live, "browser.snapshot", target)
    assert snapshot["state"] == "SUCCEEDED", snapshot
    observed = {
        **target,
        "observation_id": snapshot["result"]["observation_id"],
        "navigation_revision": snapshot["result"]["navigation_revision"],
    }
    downloaded = await execute(
        live, "browser.download", {**observed, "selector": {"by": "test_id", "value": "download"}}
    )
    assert downloaded["state"] == "SUCCEEDED", downloaded
    assert await verified_output(live, downloaded) == site["raw"]
    replay = await execute(
        live, "browser.download", {**observed, "selector": {"by": "test_id", "value": "download"}}
    )
    assert replay == downloaded and site["counts"]["downloads"] == 1
    blob = await execute(
        live,
        "browser.download",
        {**observed, "selector": {"by": "test_id", "value": "blob"}},
        key="native-blob",
        timeout_ms=5000,
    )
    assert blob["state"] == "SUCCEEDED", blob
    assert await verified_output(live, blob) == "native blob 한글".encode()
    source = live["workspace"] / "source.bin"
    source.write_bytes(site["raw"] * 2)
    owner = live["http"].headers["Authorization"].removeprefix("Bearer ")
    artifact = await ArtifactClient(
        live["gateway"], owner, live["device_id"], ca_file=live["ca"]
    ).upload(source)
    uploaded = await execute(
        live,
        "browser.upload",
        {
            **observed,
            "selector": {"by": "test_id", "value": "upload"},
            "artifact_id": artifact["id"],
            "filename": "한글.bin",
        },
    )
    assert uploaded["state"] == "SUCCEEDED", uploaded
    assert uploaded["result"]["sha256"] == artifact["sha256"]
    digest = await execute(
        live,
        "browser.evaluate",
        {
            **observed,
            "expression": "async () => { const file=document.querySelector('input').files[0];"
            "const hash=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());"
            "return {name:file.name,size:file.size,sha256:Array.from(new Uint8Array(hash))"
            ".map(b=>b.toString(16).padStart(2,'0')).join('')}; }",
        },
    )
    assert digest["state"] == "SUCCEEDED", digest
    assert digest["result"]["value"] == {
        "name": "한글.bin",
        "size": len(site["raw"]) * 2,
        "sha256": artifact["sha256"],
    }
    assert not list((live["state"] / "data/spool").glob("*.input"))
    closed = await execute(live, "browser.close", {"browser_id": target["browser_id"]})
    assert closed["result"]["cleanup_status"] == "complete"
    assert not list((live["state"] / "data/browser").iterdir())


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
    keyed = await execute(live, "browser.key", {**observed, "key": "Control+a"})
    assert keyed["state"] == "SUCCEEDED", keyed
    keyed = await execute(live, "browser.key", {**observed, "key": "Z"}, key="native-key-z")
    assert keyed["state"] == "SUCCEEDED", keyed
    value = await execute(
        live,
        "browser.evaluate",
        {**observed, "expression": "document.querySelector('input').value"},
    )
    assert value["result"]["value"] == "Z", value
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
    assert closed["state"] == "SUCCEEDED", json.dumps(closed["error"])
    assert closed["result"]["cleanup_status"] == "complete"


@pytest.mark.asyncio
async def test_browser_large_download_and_oversize_cleanup(rust_live, native_file_site):
    live, site = rust_live, native_file_site
    await online(live)
    for index, max_bytes in enumerate((100 * 1024 * 1024, 128)):
        opened = await execute(live, "browser.open", {}, key=f"large-open-{index}")
        assert opened["state"] == "SUCCEEDED", opened
        target = {
            "browser_id": opened["result"]["browser_id"],
            "page_id": opened["result"]["pages"][0]["page_id"],
        }
        navigated = await execute(
            live, "browser.navigate", {**target, "url": site["url"]}, key=f"large-nav-{index}"
        )
        assert navigated["state"] == "SUCCEEDED", navigated
        snapshot = await execute(live, "browser.snapshot", target, key=f"large-snapshot-{index}")
        assert snapshot["state"] == "SUCCEEDED", snapshot
        observed = {
            **target,
            "observation_id": snapshot["result"]["observation_id"],
            "navigation_revision": snapshot["result"]["navigation_revision"],
        }
        download = await execute(
            live,
            "browser.download",
            {**observed, "selector": {"by": "test_id", "value": "large"}, "max_bytes": max_bytes},
            key=f"large-download-{index}",
            timeout_ms=120000,
        )
        if index == 0:
            assert download["state"] == "SUCCEEDED", download
            body = await verified_output(live, download)
            expected = hashlib.sha256()
            for _ in range(100):
                expected.update(site["raw"])
            assert (
                len(body) == max_bytes and hashlib.sha256(body).hexdigest() == expected.hexdigest()
            )
            closed = await execute(
                live, "browser.close", {"browser_id": target["browser_id"]}, key="large-close"
            )
            assert closed["result"]["cleanup_status"] == "complete", closed
        else:
            assert download["error"]["code"] == "RESOURCE_EXHAUSTED", download
            assert download["error"]["execution_state"] == "unknown", download
        assert not list((live["state"] / "data/browser").iterdir())


@pytest.mark.asyncio
async def test_unsolicited_download_is_cancelled_while_agent_is_idle(rust_live, native_file_site):
    live, site = rust_live, native_file_site
    await online(live)
    opened = await execute(live, "browser.open", {})
    assert opened["state"] == "SUCCEEDED", opened
    target = {
        "browser_id": opened["result"]["browser_id"],
        "page_id": opened["result"]["pages"][0]["page_id"],
    }
    navigated = await execute(live, "browser.navigate", {**target, "url": site["url"]})
    assert navigated["state"] == "SUCCEEDED", navigated
    snapshot = await execute(live, "browser.snapshot", target)
    observed = {
        **target,
        "observation_id": snapshot["result"]["observation_id"],
        "navigation_revision": snapshot["result"]["navigation_revision"],
    }
    clicked = await execute(
        live, "browser.click", {**observed, "selector": {"by": "test_id", "value": "download"}}
    )
    assert clicked["state"] == "SUCCEEDED", clicked
    await asyncio.sleep(0.3)
    for index in range(50):
        status = await execute(live, "browser.snapshot", target, key=f"idle-snapshot-{index}")
        assert status["state"] == "SUCCEEDED", status
        if {"type": "download", "state": "unsolicited_cancelled"} in status["result"].get(
            "events", []
        ):
            break
        await asyncio.sleep(0.02)
    assert {"type": "download", "state": "unsolicited_cancelled"} in status["result"].get(
        "events", []
    ), status
    profile = live["state"] / "data/browser" / target["browser_id"]
    assert not list((profile / "downloads").glob("*"))
    closed = await execute(live, "browser.close", {"browser_id": target["browser_id"]})
    assert closed["result"]["cleanup_status"] == "complete", closed


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object crash acceptance")
async def test_controller_os_crash_kills_browser_tree_and_recovers_profile(rust_live):
    import psutil

    from .support import bridge

    live = rust_live
    await online(live)
    opened = await execute(live, "browser.open", {})
    assert opened["state"] == "SUCCEEDED", opened
    profile = live["state"] / "data" / "browser" / opened["result"]["browser_id"]
    ownership = json.loads((profile / "process.json").read_text())
    assert ownership["job_name"].startswith("Local\\RACP_")
    native = psutil.Process(ownership["pid"])
    descendants = [
        (process.pid, process.create_time()) for process in native.children(recursive=True)
    ]
    descendants.append((native.pid, native.create_time()))
    control = await bridge(live["executable"], live["state"], "status")
    controller = psutil.Process(control["pid"])
    controller.kill()
    await asyncio.to_thread(controller.wait, 10)
    for _ in range(200):
        active = []
        for pid, created in descendants:
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - created) <= 0.000001 and process.is_running():
                    active.append(pid)
            except psutil.NoSuchProcess:
                pass
        if not active:
            break
        await asyncio.sleep(0.05)
    assert not active, "owned Job descendants survived controller termination"
    started = await bridge(live["executable"], live["state"], "start")
    assert started["pid"] != control["pid"]
    for _ in range(200):
        if not profile.exists():
            break
        await asyncio.sleep(0.05)
    assert not profile.exists(), "private profile was not recovered after native Job exit"
    stopped = await bridge(live["executable"], live["state"], "stop")
    assert stopped["cleanup_status"] == "complete"
