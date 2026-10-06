import asyncio
import codecs
import hashlib
import os
import subprocess
import sys
import uuid
from typing import Any

import httpx2
import psutil
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def remote(
    live: dict[str, Any],
    operation: str,
    payload: dict[str, Any],
    *,
    profile: str = "trusted_personal",
    **extra: Any,
) -> dict[str, Any]:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": operation,
            "payload": payload,
            "idempotency_key": uuid.uuid4().hex,
            "execution_profile_id": profile,
            **extra,
        },
    )
    assert response.status_code == (202 if extra.get("execution_mode") == "job" else 200), (
        response.text
    )
    return response.json()


async def test_fs_01_bom_newline_precondition_and_idempotent_append(live: dict[str, Any]) -> None:
    root = live["workspace"]
    target = root / "한글 공백.txt"
    raw = codecs.BOM_UTF8 + "원문\r\n다음\r\n".encode()
    await asyncio.to_thread(target.write_bytes, raw)
    read = await remote(live, "filesystem.read", {"path": target.name}, profile="read_only")
    assert read["state"] == "SUCCEEDED", read
    assert read["result"]["text"] == raw.decode() and read["result"]["bom"]
    replaced = await remote(
        live,
        "filesystem.write",
        {
            "path": target.name,
            "mode": "replace",
            "overwrite": True,
            "content": "수정\n다음\n",
            "expected_sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert replaced["state"] == "SUCCEEDED", replaced
    expected = codecs.BOM_UTF8 + "수정\r\n다음\r\n".encode()
    assert await asyncio.to_thread(target.read_bytes) == expected
    stale = await remote(
        live,
        "filesystem.write",
        {
            "path": target.name,
            "mode": "replace",
            "overwrite": True,
            "content": "must not write",
            "expected_sha256": "0" * 64,
        },
    )
    assert stale["error"]["code"] == "PRECONDITION_FAILED"
    request = {
        "device_id": live["device_id"],
        "operation": "filesystem.write",
        "payload": {
            "path": target.name,
            "mode": "append",
            "content": "끝",
            "expected_offset": str(len(expected)),
        },
        "execution_profile_id": "trusted_personal",
        "idempotency_key": "append-once",
    }
    first = await live["client"].post("/api/v1/operations", json=request)
    second = await live["client"].post("/api/v1/operations", json=request)
    assert first.json() == second.json()
    assert await asyncio.to_thread(target.read_bytes) == expected + "끝".encode()


async def test_fs_path_escape_and_junction_do_not_touch_outside(live: dict[str, Any]) -> None:
    outside = live["workspace"].parent / "outside"
    await asyncio.to_thread(outside.mkdir)
    await asyncio.to_thread((outside / "keep.txt").write_text, "unchanged")
    for path in (
        str(outside / "keep.txt"),
        "../outside/keep.txt",
        r"C:drive-relative",
        r"\\host\share\file",
    ):
        denied = await remote(live, "filesystem.write", {"path": path, "content": "bad"})
        assert denied["error"]["code"] == "PATH_ACCESS_DENIED", denied
    link = live["workspace"] / "link"
    if os.name == "nt":
        result = await asyncio.to_thread(
            subprocess.run,
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
    else:
        await asyncio.to_thread(link.symlink_to, outside, target_is_directory=True)
    for operation, payload in (
        ("filesystem.read", {"path": "link/keep.txt"}),
        ("filesystem.write", {"path": "link/keep.txt", "content": "bad"}),
        ("filesystem.delete", {"path": "link", "recursive": True}),
    ):
        denied = await remote(live, operation, payload)
        assert denied["error"]["code"] == "PATH_ACCESS_DENIED", denied
    assert await asyncio.to_thread((outside / "keep.txt").read_text) == "unchanged"
    root_delete = await remote(live, "filesystem.delete", {"path": ".", "recursive": True})
    assert root_delete["error"]["code"] == "PATH_ACCESS_DENIED"


async def test_fs_copy_move_hash_pagination_and_recursive_delete(live: dict[str, Any]) -> None:
    mkdir = await remote(live, "filesystem.mkdir", {"path": "tree/nested", "parents": True})
    assert mkdir["state"] == "SUCCEEDED", mkdir
    for number in range(3):
        created = await remote(
            live, "filesystem.write", {"path": f"tree/nested/{number}.txt", "content": str(number)}
        )
        assert created["state"] == "SUCCEEDED", created
    copied = await remote(
        live, "filesystem.copy", {"source": "tree/nested/0.txt", "destination": "copy.txt"}
    )
    assert copied["state"] == "SUCCEEDED", copied
    moved = await remote(
        live, "filesystem.move", {"source": "copy.txt", "destination": "moved.txt"}
    )
    assert moved["state"] == "SUCCEEDED", moved
    hashed = await remote(live, "filesystem.hash", {"path": "moved.txt"}, profile="read_only")
    assert hashed["result"]["sha256"] == hashlib.sha256(b"0").hexdigest()
    first = await remote(
        live, "filesystem.list", {"path": "tree/nested", "limit": 2}, profile="read_only"
    )
    second = await remote(
        live,
        "filesystem.list",
        {"path": "tree/nested", "limit": 2, "cursor": first["result"]["next_cursor"]},
        profile="read_only",
    )
    assert len(first["result"]["items"]) == 2 and len(second["result"]["items"]) == 1
    stat = await remote(live, "filesystem.stat", {"path": "tree"})
    deleted = await remote(
        live,
        "filesystem.delete",
        {"path": "tree", "recursive": True, "expected_revision": stat["result"]["revision"]},
    )
    assert deleted["state"] == "SUCCEEDED", deleted
    assert not await asyncio.to_thread((live["workspace"] / "tree").exists)


async def test_fs_binary_artifact_preserves_bytes(live: dict[str, Any]) -> None:
    raw = bytes(range(256)) * 2048
    await asyncio.to_thread((live["workspace"] / "fixture.bin").write_bytes, raw)
    result = await remote(
        live, "filesystem.read", {"path": "fixture.bin", "binary": True}, profile="read_only"
    )
    assert result["state"] == "SUCCEEDED", result
    artifact_id = result["result"]["artifact_id"]
    assert artifact_id, result
    downloaded = await live["client"].get("/api/v1/artifacts/" + artifact_id + "/content")
    assert downloaded.content == raw
    metadata = (await live["client"].get("/api/v1/artifacts/" + artifact_id)).json()
    assert metadata["media_type"] == "application/octet-stream"


async def test_proc_01_pid_identity_and_wait_timeout_does_not_kill(live: dict[str, Any]) -> None:
    spawned = await remote(
        live, "process.spawn", {"argv": [sys.executable, "-c", "import time; time.sleep(90)"]}
    )
    assert spawned["state"] == "SUCCEEDED", spawned
    target = {name: spawned["result"][name] for name in ("pid", "create_time", "agent_boot_id")}
    pid = target["pid"]
    try:
        inspected = await remote(live, "process.inspect", {"pid": pid}, profile="read_only")
        assert inspected["result"]["create_time"] == target["create_time"]
        tree = await remote(live, "process.tree", target, profile="read_only")
        assert tree["result"]["items"][0]["pid"] == pid
        waited = await remote(live, "process.wait", target, profile="read_only", timeout_ms=100)
        assert waited["state"] == "TIMED_OUT" and psutil.pid_exists(pid), waited
        stale = await remote(
            live,
            "process.terminate",
            {**target, "create_time": target["create_time"] - 1, "force": True},
        )
        assert stale["error"]["code"] == "PRECONDITION_FAILED" and psutil.pid_exists(pid)
        terminated = await remote(live, "process.terminate", {**target, "force": True})
        assert terminated["state"] == "SUCCEEDED" and not psutil.pid_exists(pid), terminated
    finally:
        await live["agent"].processes.cleanup()


async def test_process_spawn_is_cleaned_on_execution_lease_expiry(live: dict[str, Any]) -> None:
    result = await remote(
        live, "process.spawn", {"argv": [sys.executable, "-c", "import time; time.sleep(90)"]}
    )
    pid = result["result"]["pid"]
    live["agent"].lease_expires = 0.0001
    for _ in range(100):
        if not psutil.pid_exists(pid):
            break
        await asyncio.sleep(0.05)
    assert not psutil.pid_exists(pid)


async def test_read_without_mutation_key_and_mcp_provider_tools(live: dict[str, Any]) -> None:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "filesystem.list",
            "payload": {"path": "."},
        },
    )
    assert response.status_code == 200 and response.json()["state"] == "SUCCEEDED", response.text
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            result = await client.call_tool(
                "fs_stat", {"device_id": live["device_id"], "path": "."}
            )
            assert (
                not result.is_error and result.structured_content["result"]["type"] == "directory"
            )
