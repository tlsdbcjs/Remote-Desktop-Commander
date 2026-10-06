import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest

pytestmark = pytest.mark.ghidra_native


async def submit(
    live: dict[str, Any], operation: str, payload: dict[str, Any], key: str | None = None
) -> str:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": operation,
            "payload": payload,
            "idempotency_key": key,
            "execution_profile_id": "trusted_personal" if key else "read_only",
            "execution_mode": "job",
            "timeout_ms": 120000,
        },
    )
    assert response.status_code == 202, response.text
    return str(response.json()["operation_id"])


async def outcome(live: dict[str, Any], identifier: str) -> dict[str, Any]:
    async with asyncio.timeout(140):
        while True:
            result = (await live["client"].get("/api/v1/operations/" + identifier)).json()
            if result["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN", "TIMED_OUT", "CANCELLED"}:
                return result
            await asyncio.sleep(0.1)


async def execute(
    live: dict[str, Any], operation: str, payload: dict[str, Any], key: str | None = None
) -> dict[str, Any]:
    result = await outcome(live, await submit(live, operation, payload, key))
    assert result["state"] == "SUCCEEDED", result
    return result["result"]


async def opened(live: dict[str, Any]) -> dict[str, Any]:
    root = Path(__file__).parents[2]
    path = root / "dist/re-fixture/manifest.json"
    if not path.is_file():
        pytest.skip("requires built known-source fixture with symbols")
    manifest = json.loads(path.read_text())
    target = Path(manifest["target"])
    assert file_hash(root / "tests/fixtures/re_program.c") == manifest["source_sha256"]
    assert file_hash(target) == manifest["target_sha256"]
    shutil.copyfile(target, live["workspace"] / "fixture.exe")
    async with asyncio.timeout(60):
        while True:
            device = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
            if any(
                "re.open" in capability["operations"]
                for capability in device["info"]["capabilities"]
            ):
                break
            await asyncio.sleep(0.1)
    result = await execute(
        live, "re.open", {"backend": "ghidra", "path": "fixture.exe"}, "native-static-open"
    )
    assert result["handle"]["analysis_state"] == "READY"
    assert result["handle"]["target_sha256"] == manifest["target_sha256"]
    return result


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def test_real_ghidra_racp_analysis_query_pagination_mutation_and_close(
    live: dict[str, Any],
) -> None:
    resource = await opened(live)
    identifier = resource["analysis_id"]
    database = Path(resource["handle"]["analysis_database"])
    assert await asyncio.to_thread(
        lambda: database.is_file() and database.with_suffix(".rep").is_dir()
    )

    async def query(action: str, **arguments: Any) -> dict[str, Any]:
        return await execute(
            live, "re.query", {"analysis_id": identifier, "action": action, **arguments}
        )

    info = await query("info")
    assert info["architecture"] == "x86:LE:64:default" and int(info["image_base"], 16) > 0
    first = await query("functions", limit=3)
    assert len(first["items"]) == 3 and first["next_cursor"]
    second = await query("functions", limit=3, cursor=first["next_cursor"])
    assert len(second["items"]) == 3 and first["items"] != second["items"]
    functions = await query("functions", limit=500)
    function = next(item for item in functions["items"] if item["name"] == "racp_add")
    address = function["address"]
    strings = await query("strings", limit=500)
    assert any("RACP known-source" in item["value"] for item in strings["items"])
    refs = await query("xrefs", address=address)
    assert refs["items"] and all(item["to"] == address for item in refs["items"])
    disassembly = await query("disassemble", address=address, limit=10)
    assert disassembly["items"][0]["address"] == address
    decompiled = await query("decompile", address=address)
    assert "racp_add" in decompiled["text"] and "racp_marker" in decompiled["text"]
    rejected = await outcome(
        live,
        await submit(
            live,
            "re.command",
            {
                "analysis_id": identifier,
                "action": "rename",
                "address": hex(int(address, 16) + 1),
                "name": "not_a_function_entry",
            },
            "native-static-invalid-rename",
        ),
    )
    assert rejected["state"] == "FAILED" and rejected["error"]["code"] == "INVALID_ARGUMENT"
    await execute(
        live,
        "re.command",
        {
            "analysis_id": identifier,
            "action": "rename",
            "address": address,
            "name": "racp_add_검증",
        },
        "native-static-rename",
    )
    await execute(
        live,
        "re.command",
        {
            "analysis_id": identifier,
            "action": "comment",
            "address": address,
            "text": "검증된 함수",
        },
        "native-static-comment",
    )
    assert "racp_add_검증" in (await query("decompile", address=address))["text"]
    assert (await query("disassemble", address=address, limit=1))["items"][0][
        "comment"
    ] == "검증된 함수"
    stale = await outcome(
        live,
        await submit(
            live,
            "re.query",
            {
                "analysis_id": identifier,
                "action": "functions",
                "limit": 3,
                "cursor": first["next_cursor"],
            },
        ),
    )
    assert stale["state"] == "FAILED" and stale["error"]["code"] == "CURSOR_EXPIRED"
    closed = await execute(live, "re.close", {"analysis_id": identifier}, "native-static-close")
    assert closed["handle"]["state"] == "CLOSED" and closed["handle"]["analysis_state"] == "CLOSED"
    assert await asyncio.to_thread(
        lambda: database.is_file() and database.with_suffix(".rep").is_dir()
    )


async def test_real_ghidra_plugin_crash_reaps_java_expires_handle_and_preserves_core(
    live: dict[str, Any],
) -> None:
    resource = await opened(live)
    backend = live["agent"].reversing.backends["ghidra"]
    operation = await submit(
        live, "re.query", {"analysis_id": resource["analysis_id"], "action": "functions"}
    )
    async with asyncio.timeout(30):
        while True:
            assert backend.owned is not None
            descendants = psutil.Process(backend.owned.process.pid).children(recursive=True)
            java = next(
                (child for child in descendants if child.name().lower() == "java.exe"), None
            )
            if java is not None:
                break
            await asyncio.sleep(0.02)
    if os.name == "nt":
        import win32api
        import win32event
        import win32job

        pinned = win32api.OpenProcess(0x101000, False, java.pid)
        assert win32job.IsProcessInJob(pinned, backend.owned.job)
    else:
        pinned = None
    try:
        backend.owned.terminate()
        result = await outcome(live, operation)
        assert result["state"] == "UNKNOWN", result
        for _ in range(100):
            if backend.owned is None:
                break
            await asyncio.sleep(0.05)
        assert backend.owned is None and backend.last_cleanup == "complete"
        assert live["agent"].reversing.resources[resource["analysis_id"]].state == "EXPIRED"
        if pinned is not None:
            assert win32event.WaitForSingleObject(pinned, 0) == 0
        assert not live["agent_task"].done()
        fs = await execute(live, "filesystem.stat", {"path": "fixture.exe"})
        assert fs["size_bytes"] > 0
    finally:
        if pinned is not None:
            pinned.Close()
