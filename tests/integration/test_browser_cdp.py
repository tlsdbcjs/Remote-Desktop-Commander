import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import playwright
import psutil
import pytest
import pytest_asyncio
from playwright.async_api import async_playwright
from racp_agent.providers.browser import BrowserProvider
from racp_agent.providers.shell import execution_env
from test_browser import browser_site as browser_site
from test_browser import call, observed, succeeded
from test_browser_files import browser_file_site as browser_file_site


@pytest_asyncio.fixture
async def external_chrome(tmp_path: Path, browser_site: str) -> Any:
    async with async_playwright() as playwright:
        directory = tmp_path / "external-profile"
        context = await playwright.chromium.launch_persistent_context(
            directory,
            headless=True,
            chromium_sandbox=True,
            env=execution_env({}),
            args=["--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1"],
        )
        port_file = directory / "DevToolsActivePort"
        port = ""
        for _ in range(200):
            try:
                lines = (
                    await asyncio.to_thread(port_file.read_text, encoding="utf-8")
                ).splitlines()
                if lines and lines[0].isdigit() and 0 < int(lines[0]) < 65536:
                    port = lines[0]
                    break
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.05)
        assert port, "Owned Chromium did not publish its debugging port within 10 seconds"
        page = context.pages[0]
        await page.goto(browser_site)
        untouched = await context.new_page()
        await untouched.goto(browser_site + "/untouched")
        try:
            yield {
                "endpoint": "http://127.0.0.1:" + port,
                "page": page,
                "untouched": untouched,
                "context": context,
                "site": browser_site,
            }
        finally:
            await context.close()


async def test_cdp_default_is_disabled_and_no_capability_is_advertised(
    live: dict[str, Any], external_chrome: dict[str, Any]
) -> None:
    device = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
    capability = next(c for c in device["info"]["capabilities"] if c["name"] == "browser")
    assert "browser.attach" not in capability["operations"]
    result = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "browser.attach",
            "payload": {"endpoint_url": external_chrome["endpoint"]},
            "idempotency_key": "disabled-cdp",
            "execution_profile_id": "trusted_personal",
        },
    )
    assert result.json()["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert not external_chrome["page"].is_closed()


@pytest.mark.browser_cdp
async def test_cdp_isolated_context_owns_only_its_pages_and_timeout_preserves_external_browser(
    live: dict[str, Any], external_chrome: dict[str, Any]
) -> None:
    external = external_chrome
    attached = await succeeded(live, "attach", {"endpoint_url": external["endpoint"]})
    assert attached["ownership"] == "external_browser_owned_context"
    target = {"browser_id": attached["browser_id"], "page_id": attached["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": external["site"]})
    snapshot = await succeeded(live, "snapshot", target)
    result = await call(
        live,
        "evaluate",
        {**observed(target, snapshot), "expression": "() => {while(true){}}"},
        timeout_ms=500,
    )
    assert result["state"] == "TIMED_OUT", result
    session = live["agent"].browsers.sessions[target["browser_id"]]
    assert session.close_result and session.close_result["cleanup_status"] == "complete"
    assert not external["page"].is_closed() and not external["untouched"].is_closed()
    assert await external["page"].title() == "RACP form"
    assert session.directory is not None and not session.directory.exists()


@pytest.mark.browser_cdp
async def test_cdp_explicit_borrowed_page_detaches_and_only_owns_new_tabs(
    live: dict[str, Any], external_chrome: dict[str, Any]
) -> None:
    external = external_chrome
    discovered = await succeeded(live, "cdp_targets", {"endpoint_url": external["endpoint"]})
    chosen = next(
        t["target_id"] for t in discovered["targets"] if t["url"] == external["site"] + "/"
    )
    attached = await succeeded(
        live,
        "attach",
        {
            "endpoint_url": external["endpoint"],
            "context_mode": "existing",
            "page_target_ids": [chosen],
        },
    )
    assert attached["ownership"] == "borrowed" and len(attached["pages"]) == 1
    target = {"browser_id": attached["browser_id"], "page_id": attached["pages"][0]["page_id"]}
    snapshot = await succeeded(live, "snapshot", target)
    await succeeded(
        live,
        "type",
        {
            **observed(target, snapshot),
            "selector": {"by": "test_id", "value": "name"},
            "text": "borrowed",
        },
    )
    assert await external["page"].get_by_test_id("name").input_value() == "borrowed"
    unsafe = await call(live, "evaluate", {**observed(target, snapshot), "expression": "() => 1"})
    assert unsafe["error"]["code"] == "OPERATION_NOT_SUPPORTED"
    new = await succeeded(live, "new_page", {"browser_id": target["browser_id"]})
    pages = await succeeded(live, "pages", {"browser_id": target["browser_id"]})
    assert (
        next(p for p in pages["pages"] if p["page_id"] == new["page_id"])["ownership"]
        == "racp_owned"
    )
    owned = {"browser_id": target["browser_id"], "page_id": new["page_id"]}
    owned_snapshot = await succeeded(live, "snapshot", owned)
    evaluated = await succeeded(
        live, "evaluate", {**observed(owned, owned_snapshot), "expression": "() => 42"}
    )
    assert evaluated["value"] == 42
    closed = await succeeded(live, "close", {"browser_id": target["browser_id"]})
    assert closed["resource_disposition"] == "external_browser_preserved"
    assert not external["page"].is_closed() and not external["untouched"].is_closed()
    assert len(external["context"].pages) == 2


@pytest.mark.browser_cdp
async def test_cdp_borrowed_evaluate_timeout_closes_only_explicitly_authorized_target(
    live: dict[str, Any], external_chrome: dict[str, Any]
) -> None:
    external = external_chrome
    discovered = await succeeded(live, "cdp_targets", {"endpoint_url": external["endpoint"]})
    chosen = next(
        t["target_id"] for t in discovered["targets"] if t["url"] == external["site"] + "/"
    )
    attached = await succeeded(
        live,
        "attach",
        {
            "endpoint_url": external["endpoint"],
            "context_mode": "existing",
            "page_target_ids": [chosen],
            "allow_page_termination": True,
        },
    )
    target = {"browser_id": attached["browser_id"], "page_id": attached["pages"][0]["page_id"]}
    snapshot = await succeeded(live, "snapshot", target)
    result = await call(
        live,
        "evaluate",
        {**observed(target, snapshot), "expression": "() => {while(true){}}"},
        timeout_ms=500,
    )
    assert result["state"] == "TIMED_OUT", result
    assert external["page"].is_closed() and not external["untouched"].is_closed()
    assert await external["untouched"].title() == "RACP form"


@pytest.mark.skipif(os.name != "nt", reason="Windows controller crash and Job cleanup gate")
async def test_cdp_controller_crash_recovers_only_persisted_authorized_targets(
    tmp_path: Path, external_chrome: dict[str, Any]
) -> None:
    external = external_chrome
    session = await external["context"].new_cdp_session(external["page"])
    chosen = (await session.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
    await session.detach()
    root = tmp_path / "controller"
    root.mkdir()
    script = """import asyncio,json,site,sys
from pathlib import Path
site.addsitedir(sys.argv[2])
from racp_agent.providers.browser import BrowserProvider
from racp_domain.models import ExecutionContext
async def main():
    spool=Path(sys.argv[1])/'spool';spool.mkdir()
    provider=BrowserProvider(spool,cdp_enabled=True)
    context=ExecutionContext('op_crash','req_crash','trace','dev_crash','owner_local','boot_crash',15000)
    result=await provider.execute('browser.attach',{
        'endpoint_url':sys.argv[3],'context_mode':'existing',
        'page_target_ids':[sys.argv[4]],'allow_page_termination':True,'width':640,'height':480},context)
    resource=provider.sessions[result['result']['browser_id']]
    print(json.dumps({'directory':str(resource.directory)}),flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        getattr(sys, "_base_executable", sys.executable),
        "-I",
        "-c",
        script,
        str(root),
        str(Path(playwright.__file__).parent.parent),
        external["endpoint"],
        chosen,
        env=execution_env({}),
        creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    children: list[psutil.Process] = []
    try:
        assert process.stdout is not None
        info = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
        children = psutil.Process(process.pid).children(recursive=True)
        process.kill()
        await process.wait()
        for _ in range(200):
            if all(not p.is_running() for p in children):
                break
            await asyncio.sleep(0.025)
        assert all(not p.is_running() for p in children)
        assert not external["page"].is_closed()
        provider = BrowserProvider(root / "spool", cdp_enabled=True)
        assert provider.temporary_cleanup["unverified"] == 1
        # The independent CDP peer confirms target removal before Playwright's
        # event reader necessarily processes the corresponding close event.
        async with external["page"].expect_event("close", timeout=5000):
            await provider.recover_remote()
        assert external["page"].is_closed() and not external["untouched"].is_closed()
        assert not await asyncio.to_thread(Path(info["directory"]).exists)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        for child in children:
            if child.is_running():
                child.kill()


@pytest.mark.browser_cdp
async def test_cdp_isolated_download_uses_private_artifacts_directory(
    live: dict[str, Any], external_chrome: dict[str, Any], browser_file_site: dict[str, Any]
) -> None:
    attached = await succeeded(live, "attach", {"endpoint_url": external_chrome["endpoint"]})
    target = {"browser_id": attached["browser_id"], "page_id": attached["pages"][0]["page_id"]}
    await succeeded(live, "navigate", {**target, "url": browser_file_site["url"]})
    snapshot = await succeeded(live, "snapshot", target)
    download = await succeeded(
        live,
        "download",
        {
            **observed(target, snapshot),
            "selector": {"by": "test_id", "value": "download"},
            "max_bytes": 8 * 1024**2,
        },
    )
    content = await live["client"].get("/api/v1/artifacts/" + download["artifact_id"] + "/content")
    assert content.content == browser_file_site["raw"]
    await succeeded(live, "close", {"browser_id": target["browser_id"]})
    assert not external_chrome["page"].is_closed() and not external_chrome["untouched"].is_closed()
