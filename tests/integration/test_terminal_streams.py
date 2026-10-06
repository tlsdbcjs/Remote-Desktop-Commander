import asyncio
import json
import subprocess
import sys
import uuid
from contextlib import aclosing
from typing import Any

import pytest
from racp_sdk.security import SecretStore
from racp_sdk.terminal import TerminalStreamClient
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus


async def terminal(live: dict[str, Any], action: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "terminal." + action,
            "payload": payload,
            "idempotency_key": uuid.uuid4().hex,
            "execution_profile_id": "trusted_personal",
        },
    )
    assert result.status_code == 200 and result.json()["state"] == "SUCCEEDED", result.text
    return result.json()["result"]


def endpoint(live: dict[str, Any], handle: str) -> str:
    return (
        live["url"].replace("http://", "ws://")
        + f"/api/v1/devices/{live['device_id']}/terminals/{handle}/stream"
    )


async def subscribe(
    socket: ClientConnection, *, cursor: str = "0", **limits: int
) -> dict[str, Any]:
    await socket.send(json.dumps({"type": "stream_open", "cursor": cursor, **limits}))
    frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
    assert frame["type"] == "stream_opened", frame
    return frame


async def through(
    socket: ClientConnection, stream: str, cursor: str, marker: str
) -> tuple[str, str]:
    data = ""
    for _ in range(100):
        frame = json.loads(await asyncio.wait_for(socket.recv(), 5))
        assert frame["type"] == "stream_data", frame
        assert frame["byte_offset"] == cursor
        data += frame["data"]
        cursor = frame["next_cursor"]
        await socket.send(
            json.dumps({"type": "stream_ack", "stream_id": stream, "byte_offset": cursor})
        )
        if marker in data:
            return data, cursor
    raise AssertionError("stream marker missing")


async def test_stream_auth_independent_consumers_and_reconnect_cursor(live: dict[str, Any]) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await terminal(live, "open", {"argv": [python, "-q", "-i"]})
    handle = opened["handle_id"]
    url = endpoint(live, handle)
    for headers, origin, status in (
        ({}, None, 401),
        ({"Authorization": "Bearer " + live["credential"]}, None, 401),
        ({"Authorization": "Bearer " + live["owner"]}, "https://evil.example", 403),
    ):
        with pytest.raises(InvalidStatus) as denied:
            async with connect(url, additional_headers=headers, origin=origin):
                pass
        assert denied.value.response.status_code == status
    headers = {"Authorization": "Bearer " + live["owner"]}
    async with (
        connect(url, additional_headers=headers) as first,
        connect(url, additional_headers=headers) as second,
    ):
        a, b = await subscribe(first), await subscribe(second)
        initial_a, cursor_a = await through(first, a["stream_id"], "0", ">>>")
        initial_b, cursor_b = await through(second, b["stream_id"], "0", ">>>")
        assert initial_a == initial_b
        await terminal(
            live,
            "write",
            {
                "handle_id": handle,
                "data": (
                    "stream_number = 41; print('STREAM-' + 'OK:', stream_number + 1, '한글🙂')\r"
                ),
            },
        )
        output_a, cursor_a = await through(first, a["stream_id"], cursor_a, "STREAM-OK: 42 한글🙂")
        output_b, _ = await through(second, b["stream_id"], cursor_b, "STREAM-OK: 42 한글🙂")
        assert output_a == output_b
    await live["restart_gateway"]()
    async with connect(url, additional_headers=headers) as resumed:
        state = await subscribe(resumed, cursor=cursor_a)
        await terminal(
            live,
            "write",
            {"handle_id": handle, "data": "print('RESUME-' + 'OK:', stream_number)\r"},
        )
        output, _ = await through(resumed, state["stream_id"], cursor_a, "RESUME-OK: 41")
        assert "STREAM-OK: 42" not in output
    await terminal(live, "close", {"handle_id": handle})


async def test_stream_credit_gap_and_control_liveness_for_slow_consumer(
    live: dict[str, Any],
) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await terminal(live, "open", {"argv": [python, "-q", "-i"]})
    handle = opened["handle_id"]
    headers = {"Authorization": "Bearer " + live["owner"]}
    async with connect(endpoint(live, handle), additional_headers=headers) as socket:
        info = await subscribe(socket, window_bytes=1024)
        stream_id = info["stream_id"]
        await terminal(live, "write", {"handle_id": handle, "data": "print('X' * 20000)\r"})
        for _ in range(200):
            sub = live["agent"].streams.subscriptions[stream_id]
            if sub.cursor - sub.consumed >= 1020 or len(sub.pending) == 4:
                break
            await asyncio.sleep(0.01)
        assert 0 < sub.cursor - sub.consumed <= 1024 and len(sub.pending) <= 4
        session = live["agent"].terminals.sessions[handle]
        session.buffer.capacity = 128
        await terminal(live, "write", {"handle_id": handle, "data": "print('Y' * 20000)\r"})
        for _ in range(200):
            if session.buffer.earliest > sub.cursor:
                break
            await asyncio.sleep(0.01)
        assert session.buffer.earliest > sub.cursor
        previous_heartbeat = live["app"].state.control.connections[live["device_id"]].last_heartbeat
        for _ in range(120):
            if (
                live["app"].state.control.connections[live["device_id"]].last_heartbeat
                > previous_heartbeat
            ):
                break
            await asyncio.sleep(0.05)
        assert (
            live["app"].state.control.connections[live["device_id"]].last_heartbeat
            > previous_heartbeat
        )
        healthy = await live["client"].post(
            "/api/v1/operations",
            json={
                "device_id": live["device_id"],
                "operation": "filesystem.stat",
                "payload": {"path": "."},
            },
        )
        assert healthy.json()["state"] == "SUCCEEDED"
        job = await live["client"].post(
            "/api/v1/operations",
            json={
                "device_id": live["device_id"],
                "operation": "shell.exec",
                "payload": {"argv": [python, "-c", "import time; time.sleep(30)"]},
                "idempotency_key": "slow-stream-cancel",
                "execution_profile_id": "trusted_personal",
                "execution_mode": "job",
            },
        )
        operation_id = job.json()["operation_id"]
        await live["client"].post("/api/v1/operations/" + operation_id + "/cancel")
        for _ in range(100):
            outcome = (await live["client"].get("/api/v1/operations/" + operation_id)).json()
            if outcome["state"] == "CANCELLED":
                break
            await asyncio.sleep(0.02)
        assert outcome["state"] == "CANCELLED", outcome
        last_cursor = "0"
        for _ in range(len(sub.pending)):
            frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
            assert frame["type"] == "stream_data" and frame["byte_offset"] == last_cursor
            last_cursor = frame["next_cursor"]
        await socket.send(
            json.dumps({"type": "stream_ack", "stream_id": stream_id, "byte_offset": last_cursor})
        )
        gap = json.loads(await asyncio.wait_for(socket.recv(), 3))
        end = json.loads(await asyncio.wait_for(socket.recv(), 3))
        assert gap["type"] == "stream_gap" and int(gap["lost_bytes"]) > 0
        assert end["type"] == "stream_end" and end["error"]["code"] == "CURSOR_EXPIRED"
    async with connect(endpoint(live, handle), additional_headers=headers) as chosen:
        await subscribe(chosen, cursor=gap["earliest_cursor"])
        frame = json.loads(await asyncio.wait_for(chosen.recv(), 3))
        assert frame["type"] == "stream_data" and frame["byte_offset"] == gap["earliest_cursor"]
    await terminal(live, "close", {"handle_id": handle})


async def test_invalid_owner_ack_closes_only_its_stream(live: dict[str, Any]) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await terminal(live, "open", {"argv": [python, "-q", "-i"]})
    handle = opened["handle_id"]
    async with connect(
        endpoint(live, handle), additional_headers={"Authorization": "Bearer " + live["owner"]}
    ) as socket:
        info = await subscribe(socket)
        await socket.send(
            json.dumps(
                {"type": "stream_ack", "stream_id": info["stream_id"], "byte_offset": "999999"}
            )
        )
        for _ in range(8):
            frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
            if frame["type"] == "stream_error":
                break
        assert frame["error"]["code"] == "INVALID_ARGUMENT"
    assert live["agent"].socket is not None
    assert live["agent"].terminals.sessions[handle].state == "OPEN"
    await terminal(live, "write", {"handle_id": handle, "data": "print(42)\r"})
    await terminal(live, "close", {"handle_id": handle})


async def test_sdk_stream_ack_and_eof(live: dict[str, Any]) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await terminal(
        live, "open", {"argv": [python, "-u", "-c", "import sys; print('SDK-한글🙂'); sys.exit(7)"]}
    )
    events = TerminalStreamClient(live["url"], live["owner"]).events(
        live["device_id"], opened["handle_id"], max_bytes=128
    )
    output = ""
    cursor = "0"
    async with aclosing(events):
        async for frame in events:
            if frame["type"] == "stream_data":
                assert frame["byte_offset"] == cursor
                cursor = frame["next_cursor"]
                output += frame["data"]
            if frame["type"] == "stream_end":
                assert frame["reason"] == "eof" and frame["process_exit"] == 7
    assert "SDK-한글🙂" in output
    assert not live["app"].state.control.streams.relays


async def test_disconnect_before_opened_reclaims_subscription_capacity(
    live: dict[str, Any],
) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await terminal(live, "open", {"argv": [python, "-q", "-i"]})
    handle = opened["handle_id"]
    for _ in range(20):
        async with connect(
            endpoint(live, handle), additional_headers={"Authorization": "Bearer " + live["owner"]}
        ) as socket:
            await socket.send('{"type":"stream_open","cursor":"0"}')
    for _ in range(200):
        if not live["app"].state.control.streams.relays and not live["agent"].streams.subscriptions:
            break
        await asyncio.sleep(0.01)
    assert not live["app"].state.control.streams.relays
    assert not live["agent"].streams.subscriptions
    assert live["agent"].terminals.sessions[handle].state == "OPEN"
    await terminal(live, "close", {"handle_id": handle})


async def test_cli_stream_emits_ndjson_and_consumed_cursor_without_secrets(
    live: dict[str, Any],
) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await terminal(
        live, "open", {"argv": [python, "-u", "-c", "print('CLI-STREAM-한글🙂')"]}
    )
    store = live["workspace"].parent / "cli-stream-owner.bin"
    SecretStore(store).save({"token": live["owner"], "gateway": live["url"]})
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "racp_cli.main",
            "--gateway",
            live["url"],
            "--owner-store",
            str(store),
            "terminal",
            "stream",
            live["device_id"],
            opened["handle_id"],
            "--max-bytes",
            "128",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    frames = [json.loads(line) for line in result.stdout.splitlines()]
    assert "CLI-STREAM-한글🙂" in "".join(frame.get("data", "") for frame in frames)
    assert frames[-1]["state"] == "COMPLETED"
    assert frames[-1]["last_cursor"] == frames[-2]["byte_offset"]
    assert live["owner"] not in result.stdout + result.stderr
    assert live["credential"] not in result.stdout + result.stderr
