import asyncio
import json
import os
import ssl

import pytest
from websockets.asyncio.client import connect

from .support import ROOT
from .test_execution import execute, online

pytestmark = pytest.mark.asyncio


async def test_native_terminal_replay_owner_close_and_stream_credit(rust_live):
    live = rust_live
    fixture = (
        ROOT
        / "target/debug/examples"
        / ("process_fixture.exe" if os.name == "nt" else "process_fixture")
    )
    assert fixture.exists()
    await online(live)
    opened = await execute(live, "terminal.open", {"argv": [str(fixture), "terminal"]})
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    newline = "\r" if os.name == "nt" else "\n"
    written = await execute(
        live, "terminal.write", {"handle_id": handle, "data": "hello 한글" + newline}
    )
    assert written["state"] == "SUCCEEDED", written
    replay = await execute(
        live, "terminal.write", {"handle_id": handle, "data": "hello 한글" + newline}
    )
    assert replay == written
    stale = await execute(
        live,
        "terminal.read",
        {"handle_id": handle, "agent_boot_id": "old_boot"},
        key="old-terminal",
    )
    assert stale["error"]["code"] == "HANDLE_EXPIRED"
    await execute(
        live,
        "terminal.write",
        {"handle_id": handle, "data": "x" * 2000 + newline},
        key="fill-buffer",
    )
    uri = (
        live["gateway"].replace("https://", "wss://")
        + f"/api/v1/devices/{live['device_id']}/terminals/{handle}/stream"
    )
    async with connect(
        uri,
        additional_headers={"Authorization": live["http"].headers["Authorization"]},
        ssl=ssl.create_default_context(cafile=str(live["ca"])),
        max_size=1024 * 1024,
        open_timeout=5,
    ) as websocket:
        await websocket.send(
            json.dumps(
                {"type": "stream_open", "cursor": "0", "max_bytes": 64, "window_bytes": 1024}
            )
        )
        ready = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert ready["type"] == "stream_opened", ready
        frames = []
        for _ in range(4):
            frame = json.loads(await asyncio.wait_for(websocket.recv(), 5))
            assert frame["type"] == "stream_data", frame
            frames.append(frame)
        # Four outstanding frames exhaust frame credit before the byte window.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(websocket.recv(), 0.2)
        await websocket.send(
            json.dumps(
                {
                    "type": "stream_ack",
                    "stream_id": ready["stream_id"],
                    "byte_offset": frames[-1]["next_cursor"],
                }
            )
        )
        resumed = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert resumed["type"] == "stream_data"
        assert resumed["byte_offset"] == frames[-1]["next_cursor"]
        assert "response=hello 한글" in "".join(frame["data"] for frame in frames)
    closed = await execute(live, "terminal.close", {"handle_id": handle})
    assert closed["state"] == "SUCCEEDED", closed
    assert closed["result"]["cleanup_status"] == "complete"
