import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from test_desktop_provider import execute

pytestmark = pytest.mark.gdb_native


async def launch(live: dict[str, Any]) -> dict[str, Any]:
    root = Path(__file__).parents[2]
    file = root / "dist/re-fixture/manifest.json"
    if not file.exists():
        pytest.skip("requires built known-source fixture with symbols")
    manifest = json.loads(file.read_text())
    source = root / "tests/fixtures/re_program.c"
    target = Path(manifest["target"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == manifest["source_sha256"]
    assert (
        hashlib.sha256(await asyncio.to_thread(target.read_bytes)).hexdigest()
        == manifest["target_sha256"]
    )
    shutil.copyfile(target, live["workspace"] / "fixture.exe")
    for _ in range(150):
        device = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
        if any("debugger.launch" in cap["operations"] for cap in device["info"]["capabilities"]):
            break
        await asyncio.sleep(0.1)
    result = await execute(
        live,
        "debugger.launch",
        {"backend": "gdb", "executable": "fixture.exe"},
        "native-debug-launch",
        "trusted_personal",
    )
    assert result["state"] == "SUCCEEDED", result
    return result["result"]


async def test_real_gdb_racp_launch_break_continue_stop_register_memory_and_close(
    live: dict[str, Any],
) -> None:
    opened = await launch(live)
    id, pid = opened["debug_id"], opened["handle"]["pid"]
    assert opened["handle"]["debugger_state"] == "STOPPED" and psutil.pid_exists(pid)
    breakpoint = await execute(
        live,
        "debugger.command",
        {"debug_id": id, "action": "set_breakpoint", "symbol": "racp_add"},
        "native-breakpoint",
        "trusted_personal",
    )
    assert breakpoint["state"] == "SUCCEEDED", breakpoint
    sequence = opened["handle"]["stop_sequence"]
    resumed = await execute(
        live,
        "debugger.command",
        {"debug_id": id, "action": "continue"},
        "native-continue",
        "trusted_personal",
    )
    assert resumed["result"]["accepted"], resumed
    stopped = await execute(live, "debugger.wait", {"debug_id": id, "after_sequence": sequence})
    assert stopped["result"]["stop_event"]["reason"] == "breakpoint-hit", stopped
    registers = await execute(live, "debugger.registers", {"debug_id": id})
    assert registers["state"] == "SUCCEEDED", registers
    pc = registers["result"]["registers"]["rip"]
    assert pc.startswith("0x") and int(pc, 16) > 0
    memory = await execute(
        live, "debugger.read_memory", {"debug_id": id, "address": pc, "size_bytes": 16}
    )
    assert len(bytes.fromhex(memory["result"]["bytes_hex"])) == 16, memory
    stack = await execute(live, "debugger.backtrace", {"debug_id": id})
    assert any(frame.get("func") == "racp_add" for frame in stack["result"]["frames"]), stack
    closed = await execute(
        live, "debugger.close", {"debug_id": id}, "native-close", "trusted_personal"
    )
    assert closed["result"]["handle"]["state"] == "CLOSED", closed
    assert not psutil.pid_exists(pid)


async def test_real_gdb_plugin_job_crash_reaps_target_and_core_stays_online(
    live: dict[str, Any],
) -> None:
    opened = await launch(live)
    backend = live["agent"].reversing.backends["gdb"]
    assert backend.owned is not None
    pid = opened["handle"]["pid"]
    if os.name == "nt":
        import win32api
        import win32event

        handle = win32api.OpenProcess(0x101000, False, pid)
    else:
        handle = None
    try:
        backend.owned.terminate()
        for _ in range(100):
            if backend.owned is None:
                break
            await asyncio.sleep(0.05)
        assert backend.owned is None and backend.last_cleanup == "complete"
        assert live["agent"].reversing.resources[opened["debug_id"]].state == "EXPIRED"
        if handle is not None:
            assert win32event.WaitForSingleObject(handle, 0) == 0
        assert not live["agent_task"].done()
        fs = await execute(live, "filesystem.stat", {"path": "fixture.exe"})
        assert fs["state"] == "SUCCEEDED"
    finally:
        if handle is not None:
            handle.Close()
