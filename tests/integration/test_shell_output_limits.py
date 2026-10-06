import errno
import os
import sys
from pathlib import Path
from typing import Any

import psutil
import pytest
from conftest import shell_request


def framed_counts(content: bytes) -> list[int]:
    counts = [0, 0]
    offset = 0
    while offset < len(content):
        channel = content[offset]
        length = int.from_bytes(content[offset + 1 : offset + 5], "big")
        assert channel in (0, 1) and 0 < length <= 8192
        assert offset + 5 + length <= len(content)
        counts[channel] += length
        offset += 5 + length
    assert offset == len(content)
    return counts


async def test_shell_combined_64_mib_cap_preserves_artifact_and_kills_tree(
    live: dict[str, Any],
) -> None:
    code = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        "Path('output-parent.pid').write_text(str(os.getpid())); "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)']); "
        "Path('output-child.pid').write_text(str(p.pid)); "
        "chunk=b'x'*1048576; "
        "[(sys.stdout.buffer if i%2==0 else sys.stderr.buffer).write(chunk) for i in range(65)]; "
        "time.sleep(90)"
    )
    request = shell_request(live, [sys.executable, "-c", code], "combined-cap")
    response = await live["client"].post("/api/v1/operations", json=request)
    outcome = response.json()
    assert outcome["state"] == "FAILED", outcome
    assert outcome["error"]["code"] == "RESOURCE_EXHAUSTED"
    result = outcome["result"]
    assert result["termination_reason"] == "output_limit"
    assert result["cleanup_status"] == "complete"
    assert result["collected_bytes"] == result["spooled_bytes"] == 64 * 1024 * 1024
    assert result["artifact_truncated"] and result["artifact_id"]
    assert "spool_path" not in outcome["error"]["details"]["partial_result"]
    assert outcome["error"]["details"]["partial_result"] == result
    for name in ("output-parent.pid", "output-child.pid"):
        assert not psutil.pid_exists(int((live["workspace"] / name).read_text()))
    content = await live["client"].get(f"/api/v1/artifacts/{result['artifact_id']}/content")
    counts = framed_counts(content.content)
    assert sum(counts) == 64 * 1024 * 1024 and min(counts) > 0
    assert (await live["client"].post("/api/v1/operations", json=request)).json() == outcome


async def test_shell_framed_spool_cap_is_a_confirmed_partial_failure(
    live: dict[str, Any],
) -> None:
    live["agent"].provider.spool_file_limit_bytes = 20000
    request = shell_request(
        live,
        [sys.executable, "-c", "import sys,time; sys.stdout.write('x'*200000); time.sleep(90)"],
    )
    request["payload"]["max_output_bytes"] = 1024
    outcome = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert outcome["state"] == "FAILED", outcome
    assert outcome["error"]["code"] == "RESOURCE_EXHAUSTED"
    result = outcome["result"]
    assert result["termination_reason"] == "spool_limit"
    assert result["cleanup_status"] == "complete" and result["artifact_id"]
    content = await live["client"].get(f"/api/v1/artifacts/{result['artifact_id']}/content")
    assert len(content.content) <= 20000
    assert sum(framed_counts(content.content)) == result["spooled_bytes"]


async def test_shell_spool_fsync_failure_keeps_collected_output(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.fsync
    fired = False

    def disk_full(fd: int) -> None:
        nonlocal fired
        # Restrict fault to the provider's known output file, not Gateway uploads or SQLite.
        if not fired and os.fstat(fd).st_size > 100000:
            fired = True
            raise OSError(errno.ENOSPC, "injected shell spool disk full")
        original(fd)

    monkeypatch.setattr(os, "fsync", disk_full)
    request = shell_request(live, [sys.executable, "-c", "print('x'*200000)"])
    outcome = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert fired and outcome["state"] == "FAILED", outcome
    assert outcome["error"]["code"] == "RESOURCE_EXHAUSTED"
    result = outcome["result"]
    assert result["termination_reason"] == "spool_write_failed"
    assert result["cleanup_status"] == "complete" and result["artifact_id"]
    content = await live["client"].get(f"/api/v1/artifacts/{result['artifact_id']}/content")
    assert sum(framed_counts(content.content)) == 200000 + len(os.linesep.encode())


async def test_shell_spool_open_failure_does_not_execute(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.open

    def disk_full(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.suffix == ".output":
            raise OSError(errno.ENOSPC, "injected shell spool disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", disk_full)
    request = shell_request(
        live, [sys.executable, "-c", "from pathlib import Path; Path('must-not-run').touch()"]
    )
    outcome = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert outcome["state"] == "FAILED", outcome
    assert outcome["error"]["code"] == "RESOURCE_EXHAUSTED"
    assert outcome["error"]["execution_state"] == "not_started"
    assert not (live["workspace"] / "must-not-run").exists()
