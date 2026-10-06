import asyncio
import errno
import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_filesystem_process import remote


async def test_recursive_copy_job_and_partial_failure_report(
    live: dict[str, Any], monkeypatch: Any
) -> None:
    root = live["workspace"]
    await asyncio.to_thread((root / "source").mkdir)
    await asyncio.to_thread((root / "source" / "a.txt").write_text, "a")
    await asyncio.to_thread((root / "source" / "b.txt").write_text, "b")
    good = await remote(
        live, "filesystem.copy", {"source": "source", "destination": "good", "recursive": True}
    )
    assert good["state"] == "SUCCEEDED", good
    assert await asyncio.to_thread((root / "good" / "b.txt").read_text) == "b"
    filesystem = live["agent"].filesystem
    original = filesystem.copy_file
    count = 0

    def disk_full(
        source: Path, destination: Path, payload: dict[str, Any], budget: Any
    ) -> dict[str, Any]:
        nonlocal count
        count += 1
        if count == 2:
            raise OSError(errno.ENOSPC, "injected disk full")
        return original(source, destination, payload, budget)

    monkeypatch.setattr(filesystem, "copy_file", disk_full)
    failed = await remote(
        live, "filesystem.copy", {"source": "source", "destination": "partial", "recursive": True}
    )
    assert failed["state"] == "FAILED", failed
    assert failed["error"]["details"]["cleanup_status"] == "partial"
    assert "report_spool_path" not in failed["error"]["details"]
    artifact_id = failed["error"]["details"]["report_artifact_id"]
    response = await live["client"].get("/api/v1/artifacts/" + artifact_id + "/content")
    report = json.loads(response.content)
    assert str(root / "partial" / "a.txt") in report["completed"]
    assert str(root / "partial" / "b.txt") in report["failed"]
    assert await asyncio.to_thread((root / "source" / "b.txt").read_text) == "b"


async def test_file_create_is_no_overwrite_and_cursor_is_scope_bound(live: dict[str, Any]) -> None:
    missing = await remote(live, "filesystem.read", {"path": "missing-parent/file"})
    assert missing["error"]["code"] == "PATH_NOT_FOUND", missing
    created = await remote(live, "filesystem.write", {"path": "a", "content": "first"})
    assert created["state"] == "SUCCEEDED"
    duplicate = await remote(live, "filesystem.write", {"path": "a", "content": "bad"})
    assert duplicate["error"]["code"] == "CONFLICT"
    await remote(live, "filesystem.write", {"path": "b", "content": "b"})
    first = await remote(live, "filesystem.list", {"path": ".", "limit": 1}, profile="read_only")
    changed = await remote(
        live,
        "filesystem.list",
        {"path": ".", "limit": 2, "cursor": first["result"]["next_cursor"]},
        profile="read_only",
    )
    assert changed["error"]["code"] == "CURSOR_EXPIRED"
    assert await asyncio.to_thread((live["workspace"] / "a").read_text) == "first"


@pytest.mark.skipif(os.name != "nt", reason="Windows directory share-mode test")
async def test_windows_directory_lock_prevents_ancestor_swap(live: dict[str, Any]) -> None:
    directory = live["workspace"] / "locked"
    await asyncio.to_thread(directory.mkdir)

    def attempt() -> None:
        with live["agent"].filesystem.guard.directory(directory):
            if os.name == "nt":
                try:
                    directory.rename(directory.with_name("swapped"))
                except PermissionError:
                    return
                raise AssertionError(
                    "directory rename was permitted while the guard held its handle"
                )

    await asyncio.to_thread(attempt)
    if os.name == "nt":
        assert await asyncio.to_thread(directory.exists)


@pytest.mark.skipif(os.name == "nt", reason="POSIX dirfd test requires a real POSIX runner")
async def test_posix_dirfd_does_not_follow_swapped_symlink(live: dict[str, Any]) -> None:
    root = live["workspace"]
    directory = root / "original"
    moved = root / "moved"
    outside = root.parent / "outside-dirfd"
    await asyncio.to_thread(directory.mkdir)
    await asyncio.to_thread(outside.mkdir)

    def attempt() -> None:
        with live["agent"].filesystem.guard.directory(directory) as parent:
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
            with os.fdopen(
                parent.open("owned", os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
            ) as stream:
                stream.write(b"owned")
        assert (moved / "owned").read_bytes() == b"owned"
        assert not (outside / "owned").exists()

    await asyncio.to_thread(attempt)
