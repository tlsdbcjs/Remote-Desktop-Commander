import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from racp_agent.broker.identity import process_identity
from racp_agent.broker.supervisor import BrokerSupervisor


@pytest.fixture
def synthetic_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    script = Path(__file__).parents[1] / "fixtures/synthetic_desktop_broker.py"
    monkeypatch.setattr(
        BrokerSupervisor,
        "_command",
        lambda self, path: [sys.executable, "-I", str(script), "--pairing", str(path)],
    )


@pytest.fixture
def synthetic_live(synthetic_desktop: None, live: dict[str, Any]) -> dict[str, Any]:
    return live


async def execute(
    live: dict[str, Any],
    operation: str,
    payload: dict[str, Any],
    key: str | None = None,
    profile: str = "read_only",
    **extra: Any,
) -> dict[str, Any]:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": operation,
            "payload": payload,
            "idempotency_key": key,
            "execution_profile_id": profile,
            **extra,
        },
    )
    assert response.status_code < 400, response.text
    identifier = response.json()["operation_id"]
    for _ in range(200):
        outcome = (await live["client"].get("/api/v1/operations/" + identifier)).json()
        if outcome["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED", "TIMED_OUT"}:
            return outcome
        await asyncio.sleep(0.025)
    pytest.fail("desktop operation did not finish")


@pytest.mark.desktop
async def test_desktop_real_supervisor_wire_policy_identity_and_session_gate(
    live: dict[str, Any],
) -> None:
    session_id = process_identity(os.getpid()).session
    result = await execute(live, "desktop.sessions", {})
    assert result["state"] == "SUCCEEDED"
    assert result["result"]["sessions"][0]["session_id"] == session_id
    broker = live["agent"].desktop.sessions[session_id]
    assert (
        broker.process is not None and broker.process.returncode is None and broker.job is not None
    )
    pids = broker.protected_pids()
    assert len(pids) >= 2
    pid = next(pid for pid in pids if pid != broker.process.pid)
    blocked = await execute(
        live,
        "process.terminate",
        {
            "pid": pid,
            "create_time": process_identity(pid).created,
            "agent_boot_id": live["agent"].boot_id,
            "force": True,
        },
        "protected-broker",
        "trusted_personal",
    )
    assert blocked["state"] == "FAILED" and blocked["error"]["code"] == "PERMISSION_DENIED"
    denied = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "desktop.lease_acquire",
            "payload": {"session_id": session_id},
            "idempotency_key": "lease-read-only",
            "execution_profile_id": "read_only",
        },
    )
    assert denied.status_code == 403
    wrong = await execute(live, "desktop.monitors", {"session_id": session_id + 100})
    assert wrong["state"] == "FAILED" and wrong["error"]["code"] == "SESSION_UNAVAILABLE"
    observed = await execute(live, "desktop.monitors", {"session_id": session_id})
    if result["result"]["sessions"][0]["available"]:
        assert observed["state"] == "SUCCEEDED" and observed["result"]["monitors"]
    else:
        assert observed["state"] == "FAILED" and observed["error"]["code"] in {
            "SESSION_LOCKED",
            "SESSION_UNAVAILABLE",
        }
        device = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
        assert device["info"]["status"] == "ONLINE"
        assert (await live["client"].get("/api/v1/doctor")).json()["status"] == "degraded"
        assert (await execute(live, "filesystem.stat", {"path": "."}))["state"] == "SUCCEEDED"
    path = broker.path
    await live["agent"].desktop.cleanup()
    assert path is not None and not path.exists()
    import psutil

    for _ in range(500):
        if not any(psutil.pid_exists(pid) for pid in pids):
            break
        await asyncio.sleep(0.01)
    assert not any(psutil.pid_exists(pid) for pid in pids)


@pytest.mark.skipif(os.name != "nt", reason="native GDI memory DC")
def test_native_capture_top_down_pixels_png_preview_and_gdi_cleanup() -> None:
    import ctypes
    import io
    import struct
    import zlib

    import win32gui
    from PIL import Image
    from racp_agent.broker.capture import dib, png, preview

    source = win32gui.CreateCompatibleDC(0)
    bitmap = win32gui.CreateBitmap(3, 2, 1, 32, None)
    previous = win32gui.SelectObject(source, bitmap)
    try:
        for y in range(2):
            for x in range(3):
                win32gui.SetPixel(source, x, y, (x * 90) | (y * 110 << 8) | (50 << 16))
        data = dib(source, 0, 0, 3, 2)
        encoded = png(3, 2, data)
        assert encoded.startswith(b"\x89PNG\r\n\x1a\n")
        offset, compressed = 8, bytearray()
        while offset < len(encoded):
            length = struct.unpack(">I", encoded[offset : offset + 4])[0]
            kind, chunk = (
                encoded[offset + 4 : offset + 8],
                encoded[offset + 8 : offset + 8 + length],
            )
            assert (
                zlib.crc32(kind + chunk)
                == struct.unpack(">I", encoded[offset + 8 + length : offset + 12 + length])[0]
            )
            if kind == b"IDAT":
                compressed.extend(chunk)
            offset += length + 12
        rows = zlib.decompress(compressed)
        assert rows[1:4] == bytes([0, 0, 50]) and rows[11:14] == bytes([0, 110, 50])
        small, metadata = preview(3, 2, data)
        with Image.open(io.BytesIO(small)) as decoded:
            assert decoded.getpixel((0, 0)) == (0, 0, 50)
            assert decoded.getpixel((0, 1)) == (0, 110, 50)
        assert metadata["physical_pixels_per_preview_pixel_x"] == 1
        user = ctypes.WinDLL("user32")
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        user.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        before = user.GetGuiResources(kernel.GetCurrentProcess(), 0)
        for _ in range(100):
            dib(source, 0, 0, 3, 2)
        assert user.GetGuiResources(kernel.GetCurrentProcess(), 0) == before
    finally:
        win32gui.SelectObject(source, previous)
        win32gui.DeleteObject(bitmap)
        win32gui.DeleteDC(source)


@pytest.mark.desktop
async def test_desktop_capture_actual_pipe_chunk_hash_artifacts_and_replay(
    synthetic_live: dict[str, Any],
) -> None:
    live = synthetic_live
    session_id = process_identity(os.getpid()).session
    screenshot = await execute(
        live, "desktop.screenshot", {"session_id": session_id}, "capture-once"
    )
    assert screenshot["state"] == "SUCCEEDED", screenshot
    result = screenshot["result"]
    assert result["capture_scope"] == "synthetic_fixture" and result["count"] == 1
    assert result["crop_origin"] == {"x": -1920, "y": -300}
    assert result["monitors"][0]["scale_x"] == 1.5
    for item in (result, result["preview"]):
        artifact = (await live["client"].get("/api/v1/artifacts/" + item["artifact_id"])).json()
        data = (
            await live["client"].get("/api/v1/artifacts/" + item["artifact_id"] + "/content")
        ).content
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 32768
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
    outputs = (
        await live["client"].get("/api/v1/operations/" + screenshot["operation_id"] + "/outputs")
    ).json()
    assert len(outputs["items"]) == 2
    replay = await execute(live, "desktop.screenshot", {"session_id": session_id}, "capture-once")
    assert replay["operation_id"] == screenshot["operation_id"] and replay["result"] == result
    import base64

    import httpx
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            content = await client.call_tool(
                "desktop_screenshot", {"device_id": live["device_id"], "session_id": session_id}
            )
            assert not content.is_error and content.structured_content is not None
            images = [item for item in content.content if item.type == "image"]
            assert len(images) == 1 and base64.b64decode(images[0].data).startswith(
                b"\x89PNG\r\n\x1a\n"
            )


@pytest.mark.desktop
@pytest.mark.parametrize("deadline", [False, True])
async def test_desktop_cancel_hung_broker_confirms_job_cleanup_without_input(
    synthetic_live: dict[str, Any],
    deadline: bool,
) -> None:
    live = synthetic_live
    session_id = process_identity(os.getpid()).session
    lease = (
        await execute(
            live,
            "desktop.lease_acquire",
            {"session_id": session_id},
            "input-lease",
            "trusted_personal",
        )
    )["result"]
    observed = (await execute(live, "desktop.windows", {"session_id": session_id}))["result"]
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "desktop.type",
            "execution_mode": "job",
            "execution_profile_id": "trusted_personal",
            "idempotency_key": "hung-fixture-input",
            "timeout_ms": 500 if deadline else 10000,
            "payload": {
                "session_id": session_id,
                "lease_id": lease["lease_id"],
                "observation_id": observed["observation_id"],
                "layout_revision": observed["layout_revision"],
                "expected_window_id": "window_synthetic",
                "text": "fixture_hang",
            },
        },
    )
    assert response.status_code == 202
    identifier = response.json()["operation_id"]
    for _ in range(100):
        if live["agent"].journal.get(identifier)["state"] == "RUNNING":
            break
        await asyncio.sleep(0.01)
    broker = live["agent"].desktop.sessions[session_id]
    pids = broker.protected_pids()
    await asyncio.sleep(0.15)
    if not deadline:
        await live["client"].post("/api/v1/operations/" + identifier + "/cancel")
    for _ in range(200):
        operation = (await live["client"].get("/api/v1/operations/" + identifier)).json()
        if operation["state"] in {"CANCELLED", "TIMED_OUT", "UNKNOWN"}:
            break
        await asyncio.sleep(0.025)
    assert operation["state"] == ("TIMED_OUT" if deadline else "CANCELLED"), operation
    assert operation["error"]["execution_state"] == "unknown"
    assert operation["error"]["details"]["cleanup_status"] == "complete"
    import psutil

    assert broker.process is None and broker.cleanup_status == "complete"
    assert not any(psutil.pid_exists(pid) for pid in pids)
