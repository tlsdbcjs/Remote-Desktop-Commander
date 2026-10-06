import asyncio
import hashlib
from dataclasses import replace
from typing import Any

import pytest
from test_desktop_provider import execute


async def ready(live: dict[str, Any]) -> None:
    for _ in range(150):
        response = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
        if any("re.open" in cap["operations"] for cap in response["info"]["capabilities"]):
            return
        if live["agent_task"].done():
            await live["agent_task"]
        await asyncio.sleep(0.1)
    raise AssertionError("installed plugin never became ready")


async def open_analysis(live: dict[str, Any], key: str = "analysis-open") -> dict[str, Any]:
    (live["workspace"] / "fixture.bin").write_bytes(b"owned synthetic domain fixture")
    result = await execute(
        live,
        "re.open",
        {"backend": "domain-fixture", "path": "fixture.bin"},
        key,
        "trusted_personal",
    )
    assert result["state"] == "SUCCEEDED", result
    return result


@pytest.mark.re_plugin
async def test_static_domain_handle_journal_scope_pagination_and_mutation(
    live: dict[str, Any],
) -> None:
    await ready(live)
    opened = await open_analysis(live)
    id = opened["result"]["analysis_id"]
    assert (await open_analysis(live))["result"]["analysis_id"] == id
    assert len(live["agent"].reversing.resources) == 1
    handle = (await live["client"].get("/api/v1/handles/" + id)).json()
    assert (
        handle["type"] == "analysis"
        and handle["target_sha256"] == hashlib.sha256(b"owned synthetic domain fixture").hexdigest()
    )
    query = {"analysis_id": id, "action": "functions", "limit": 2}
    page = await execute(live, "re.query", query)
    assert len(page["result"]["items"]) == 2 and page["result"]["next_cursor"]
    second = await execute(live, "re.query", {**query, "cursor": page["result"]["next_cursor"]})
    assert second["result"]["items"][0]["name"] == "synthetic_2"
    mismatch = await execute(
        live, "re.query", {**query, "action": "strings", "cursor": page["result"]["next_cursor"]}
    )
    assert mismatch["error"]["code"] == "CURSOR_EXPIRED"
    resource = live["agent"].reversing.resources[id]
    context = resource.context
    denied = await live["agent"].reversing.execute(
        "re.query",
        {"analysis_id": id, "action": "info"},
        replace(context, principal_id="owner_foreign"),
    )
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    renamed = await execute(
        live,
        "re.command",
        {
            "analysis_id": id,
            "action": "rename",
            "address": "0x140001000",
            "name": "renamed_fixture",
        },
        "rename-once",
        "trusted_personal",
    )
    assert renamed["state"] == "SUCCEEDED", renamed
    names = await execute(live, "re.query", query)
    assert names["result"]["items"][0]["name"] == "renamed_fixture"
    stale = await execute(live, "re.query", {**query, "cursor": page["result"]["next_cursor"]})
    assert stale["error"]["code"] == "CURSOR_EXPIRED"
    unsupported = await execute(
        live, "re.query", {"analysis_id": id, "action": "decompile", "address": "0x140001000"}
    )
    assert unsupported["error"]["code"] == "OPERATION_NOT_SUPPORTED"
    closed = await execute(
        live, "re.close", {"analysis_id": id}, "close-analysis", "trusted_personal"
    )
    assert closed["result"]["handle"]["state"] == "CLOSED"
    expired = await execute(live, "re.query", {"analysis_id": id, "action": "info"})
    assert expired["error"]["code"] == "HANDLE_EXPIRED"


@pytest.mark.re_plugin
async def test_debugger_acceptance_stop_wait_and_binary_memory_artifact(
    live: dict[str, Any],
) -> None:
    await ready(live)
    (live["workspace"] / "fixture.bin").write_bytes(b"synthetic debugger target")
    launched = await execute(
        live,
        "debugger.launch",
        {"backend": "domain-fixture", "executable": "fixture.bin"},
        "debug-launch",
        "trusted_personal",
    )
    assert launched["state"] == "SUCCEEDED", launched
    id = launched["result"]["debug_id"]
    initial = await execute(live, "debugger.wait", {"debug_id": id, "after_sequence": "0"})
    assert initial["result"]["stop_event"]["sequence"] == "1"
    accepted = await execute(
        live,
        "debugger.command",
        {"debug_id": id, "action": "continue"},
        "debug-continue",
        "trusted_personal",
    )
    assert accepted["result"]["accepted"]
    stopped = await execute(live, "debugger.wait", {"debug_id": id, "after_sequence": "1"})
    assert stopped["result"]["stop_event"]["sequence"] == "2"
    registers = await execute(live, "debugger.registers", {"debug_id": id})
    assert registers["result"]["registers"]["rip"] == "0x140001000"
    read = await execute(
        live,
        "debugger.read_memory",
        {"debug_id": id, "address": "0x140001000", "size_bytes": 65536},
    )
    assert read["state"] == "SUCCEEDED", read
    artifact = read["result"]["artifact_id"]
    content = await live["client"].get("/api/v1/artifacts/" + artifact + "/content")
    assert content.content == b"\x90" * 65536
    await live["restart_agent"]()
    await ready(live)
    expired = await execute(live, "debugger.info", {"debug_id": id})
    assert expired["error"]["code"] == "HANDLE_EXPIRED"


@pytest.mark.re_plugin(mode="crash")
async def test_plugin_crash_keeps_actual_agent_online_and_expires_handles(
    live: dict[str, Any],
) -> None:
    await ready(live)
    opened = await open_analysis(live)
    id = opened["result"]["analysis_id"]
    failed = await execute(live, "re.query", {"analysis_id": id, "action": "info"})
    assert failed["state"] == "UNKNOWN", failed
    assert not live["agent_task"].done()
    assert live["agent"].reversing.resources[id].state == "EXPIRED"
    fs = await execute(live, "filesystem.stat", {"path": "fixture.bin"})
    assert fs["state"] == "SUCCEEDED"
    device = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
    assert device["info"]["status"] == "ONLINE"


@pytest.mark.re_plugin(mode="unsafe_file")
async def test_plugin_cannot_return_a_file_upload_descriptor(live: dict[str, Any]) -> None:
    await ready(live)
    opened = await open_analysis(live)
    failed = await execute(
        live, "re.query", {"analysis_id": opened["result"]["analysis_id"], "action": "info"}
    )
    assert failed["error"]["code"] == "PLUGIN_PROTOCOL_ERROR"
    assert failed["result"] is None
    assert live["agent"].reversing.backends["domain-fixture"].owned is None
