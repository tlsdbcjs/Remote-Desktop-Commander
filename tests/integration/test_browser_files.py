import asyncio
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import playwright
import psutil
import pytest
from racp_agent.providers.browser_workspace import BrowserWorkspace
from racp_agent.providers.shell import execution_env
from racp_domain.models import RACPError
from racp_sdk.artifacts import ArtifactClient
from test_browser import browser_site as browser_site
from test_browser import call, observed, succeeded


@pytest.fixture
def browser_file_site(browser_site: str) -> Any:
    raw = bytes(range(256)) * 16384 + b"RACP-END"
    state: dict[str, Any] = {"downloads": 0, "uploads": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in {"/file", "/slow"}:
                if "profile=verified" not in self.headers.get("Cookie", ""):
                    self.send_response(403)
                    self.end_headers()
                    return
                state["downloads"] += 1
                self.send_response(200)
                self.send_header("Content-Disposition", 'attachment; filename="../../server.exe"')
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    for start in range(0, len(raw), 65536):
                        self.wfile.write(raw[start : start + 65536])
                        self.wfile.flush()
                        if self.path == "/slow":
                            threading.Event().wait(0.01)
                except (ConnectionError, OSError):
                    pass
                finally:
                    state["finished"] = state.get("finished", 0) + 1
                return
            body = b"""<!doctype html><title>Files</title>
            <a data-testid="download" href="/file">Download</a>
            <a data-testid="slow" href="/slow">Slow download</a>
            <button data-testid="blob" onclick="const a=document.createElement('a');
                a.href=window.URL.createObjectURL(new Blob([new Uint8Array([0,1,255])]));
                a.download='blob.bin';a.click()">Blob</button>
            <label>Upload<input type="file" data-testid="upload" onchange="
                const f=this.files[0]; fetch('/upload',{method:'POST',body:f}).then(r=>r.json())
                .then(v=>document.getElementById('status').textContent=
                    JSON.stringify({...v,name:f.name}))"></label><pre id="status"></pre>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "profile=verified; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            content = self.rfile.read(int(self.headers["Content-Length"]))
            state.update(
                uploads=state["uploads"] + 1,
                upload_sha256=hashlib.sha256(content).hexdigest(),
                upload_size=len(content),
            )
            result = json.dumps({"sha256": state["upload_sha256"], "size": len(content)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{server.server_port}", "raw": raw, "state": state}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


async def page(live: dict[str, Any], site: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    opened = await succeeded(live, "open", {})
    target = {"browser_id": opened["browser_id"], "page_id": opened["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": site["url"]})
    return target, await succeeded(live, "snapshot", target)


async def test_native_browser_download_cookie_blob_artifact_dedupe_and_default_cancel(
    live: dict[str, Any], browser_file_site: dict[str, Any]
) -> None:
    site = browser_file_site
    target, snapshot = await page(live, site)
    data = {
        **observed(target, snapshot),
        "selector": {"by": "test_id", "value": "download"},
        "max_bytes": 8 * 1024**2,
    }
    downloaded = await succeeded(live, "download", data, idempotency_key="native-download")
    assert downloaded["native_cleanup"] == "complete"
    assert downloaded["size_bytes"] == len(site["raw"])
    content = await live["client"].get(
        "/api/v1/artifacts/" + downloaded["artifact_id"] + "/content"
    )
    assert content.content == site["raw"]
    assert hashlib.sha256(content.content).hexdigest() == downloaded["sha256"]
    assert await succeeded(live, "download", data, idempotency_key="native-download") == downloaded
    assert site["state"]["downloads"] == 1
    blob = await succeeded(
        live,
        "download",
        {
            **observed(target, snapshot),
            "selector": {"by": "test_id", "value": "blob"},
            "max_bytes": 100,
        },
        timeout_ms=5000,
    )
    assert (
        await live["client"].get("/api/v1/artifacts/" + blob["artifact_id"] + "/content")
    ).content == bytes([0, 1, 255])
    await succeeded(
        live,
        "click",
        {**observed(target, snapshot), "selector": {"by": "test_id", "value": "slow"}},
    )
    # No further browser API call: cancellation must run while the worker is idle.
    for _ in range(100):
        if site["state"].get("finished", 0) == 2:
            break
        await asyncio.sleep(0.02)
    assert site["state"].get("finished", 0) == 2
    for _ in range(100):
        status = await succeeded(live, "snapshot", target)
        if {"type": "download", "state": "unsolicited_cancelled"} in status["events"]:
            break
        await asyncio.sleep(0.01)
    assert {"type": "download", "state": "unsolicited_cancelled"} in status["events"]
    session = live["agent"].browsers.sessions[target["browser_id"]]
    assert session.directory is not None
    assert list((session.directory / "downloads").iterdir()) == []
    root = session.directory
    await succeeded(live, "close", {"browser_id": target["browser_id"]})
    assert not root.exists()


async def test_browser_upload_materializes_only_assigned_artifact_and_verifies_real_page_bytes(
    live: dict[str, Any], browser_file_site: dict[str, Any]
) -> None:
    site = browser_file_site
    target, snapshot = await page(live, site)
    source = live["workspace"].parent / "artifact-input.bin"
    source.write_bytes(site["raw"])
    artifact = await ArtifactClient(live["url"], live["owner"], live["device_id"]).upload(source)
    data = {
        **observed(target, snapshot),
        "selector": {"by": "test_id", "value": "upload"},
        "artifact_id": artifact["id"],
        "filename": "한글-input.bin",
    }
    uploaded = await succeeded(live, "upload", data, idempotency_key="browser-upload-file")
    assert uploaded["sha256"] == hashlib.sha256(site["raw"]).hexdigest()
    for _ in range(100):
        current = await succeeded(live, "snapshot", target)
        if "한글-input.bin" in current["semantic_tree"]:
            break
        await asyncio.sleep(0.02)
    assert "한글-input.bin" in current["semantic_tree"]
    assert site["state"]["upload_size"] == len(site["raw"])
    assert site["state"]["upload_sha256"] == artifact["sha256"]
    assert await succeeded(live, "upload", data, idempotency_key="browser-upload-file") == uploaded
    assert site["state"]["uploads"] == 1
    session = live["agent"].browsers.sessions[target["browser_id"]]
    assert session.directory is not None
    assert len(list((session.directory / "uploads").iterdir())) == 1
    assert uploaded["staging_retention"] == "browser_lifetime"
    assert list(live["agent"].spool.glob("*.input")) == []
    directory = session.directory
    closed = await succeeded(live, "close", {"browser_id": target["browser_id"]})
    assert closed["cleanup_status"] == "complete", closed
    assert not directory.exists()


async def test_browser_oversize_download_closes_native_worker_without_publishing_partial_artifact(
    live: dict[str, Any], browser_file_site: dict[str, Any]
) -> None:
    target, snapshot = await page(live, browser_file_site)
    session = live["agent"].browsers.sessions[target["browser_id"]]
    outcome = await call(
        live,
        "download",
        {
            **observed(target, snapshot),
            "selector": {"by": "test_id", "value": "slow"},
            "max_bytes": 65536,
        },
    )
    assert outcome["state"] == "FAILED" and outcome["error"]["code"] == "RESOURCE_EXHAUSTED", (
        outcome
    )
    assert outcome["error"]["execution_state"] == "unknown"
    assert session.close_result and session.close_result["cleanup_status"] == "complete"
    assert session.directory is not None and not session.directory.exists()
    assert not (live["agent"].spool / (outcome["operation_id"] + ".download")).exists()
    assert not (live["agent"].spool / (outcome["operation_id"] + ".input")).exists()
    assert (
        await live["client"].get("/api/v1/operations/" + outcome["operation_id"] + "/outputs")
    ).json()["items"] == []


async def test_completed_browser_download_recovers_attachment_after_agent_restart_without_reclick(
    live: dict[str, Any], browser_file_site: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    target, snapshot = await page(live, browser_file_site)

    async def unavailable(*args: Any, **kwargs: Any) -> str:
        raise RACPError("TRANSFER_INTERRUPTED", "injected unavailable Artifact store")

    monkeypatch.setattr(live["agent"], "_upload", unavailable)
    outcome = await call(
        live,
        "download",
        {
            **observed(target, snapshot),
            "selector": {"by": "test_id", "value": "download"},
            "max_bytes": 8 * 1024**2,
        },
        idempotency_key="download-recovery",
    )
    assert (
        outcome["state"] == "SUCCEEDED" and outcome["result"]["artifact_upload_status"] == "pending"
    )
    await live["restart_agent"]()
    for _ in range(200):
        outputs = (
            await live["client"].get("/api/v1/operations/" + outcome["operation_id"] + "/outputs")
        ).json()["items"]
        if outputs and outputs[0]["artifact_id"]:
            break
        await asyncio.sleep(0.025)
    assert outputs[0]["artifact_id"]
    content = await live["client"].get(
        "/api/v1/artifacts/" + outputs[0]["artifact_id"] + "/content"
    )
    assert content.content == browser_file_site["raw"]
    assert browser_file_site["state"]["downloads"] == 1
    unchanged = (await live["client"].get("/api/v1/operations/" + outcome["operation_id"])).json()
    assert unchanged["result"] == outcome["result"]


@pytest.mark.skipif(os.name != "nt", reason="Windows KILL_ON_JOB_CLOSE owner-crash gate")
async def test_owned_browser_files_recovered_after_controller_is_killed(
    tmp_path: Path, browser_site: str
) -> None:
    directory = tmp_path / "crashed-controller"
    directory.mkdir()
    script = """import asyncio,json,site,sys
from pathlib import Path
site.addsitedir(sys.argv[2])
from racp_agent.providers.browser import BrowserProvider
from racp_domain.models import ExecutionContext
async def main():
    spool=Path(sys.argv[1])/'spool';spool.mkdir()
    provider=BrowserProvider(spool)
    context=ExecutionContext('op_crash','req_crash','trace','dev_crash','owner_local','boot_crash',15000)
    result=await provider.execute('browser.open',{'headless':True,'width':640,'height':480},context)
    session=provider.sessions[result['result']['browser_id']]
    print(json.dumps({'worker':session.process.pid,'directory':str(session.directory)}),flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
    env = execution_env({})
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    process = await asyncio.create_subprocess_exec(
        getattr(sys, "_base_executable", sys.executable),
        "-I",
        "-c",
        script,
        str(directory),
        str(Path(playwright.__file__).parent.parent),
        env=env,
        creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    children: list[psutil.Process] = []
    try:
        assert process.stdout is not None
        info = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
        owned = Path(info["directory"])
        children = psutil.Process(process.pid).children(recursive=True)
        assert any(child.pid == info["worker"] for child in children)
        process.kill()
        await process.wait()
        for _ in range(200):
            if all(not child.is_running() for child in children):
                break
            await asyncio.sleep(0.025)
        assert all(not child.is_running() for child in children)
        assert await asyncio.to_thread(owned.exists)
        cleanup = await asyncio.to_thread(BrowserWorkspace(directory / "browsers").collect)
        assert cleanup["removed"] == 1 and not await asyncio.to_thread(owned.exists)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        for child in children:
            if child.is_running():
                child.kill()
