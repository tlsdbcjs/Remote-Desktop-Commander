"""Real Windows DbgHelp on an owned harmless process; no debugger installation."""

import ctypes
import hashlib
import importlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest


def worker():
    assert importlib.util.find_spec("racp_agent.process_dump"), "Typed dump worker missing"
    return importlib.import_module("racp_agent.process_dump")


def ready_target(tmp_path: Path) -> subprocess.Popen:
    ready = tmp_path / "target.ready"
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys,time;from pathlib import Path;marker=b'RACP_OWNED_DUMP_TEST';"
            "Path(sys.argv[1]).write_text('ready');time.sleep(60)",
            str(ready),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if ready.exists():
            return process
        if process.poll() is not None:
            pytest.fail("Owned target exited before readiness")
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    pytest.fail("Owned target startup expired")


def test_dump_io_rewrites_are_bounded_before_any_byte_is_written() -> None:
    stream = io.BytesIO()
    writer = worker().BudgetedDumpWriter(stream, 32)
    writer.write(8, b"data")
    writer.write(0, b"MDMP")
    original = stream.getvalue()
    with pytest.raises(worker().DumpBudgetExceeded):
        writer.write(31, b"xx")
    assert stream.getvalue() == original
    assert writer.size == 12


@pytest.mark.skipif(os.name != "nt", reason="Windows DbgHelp HRESULT")
@pytest.mark.parametrize("code", [5, -2147024891])
def test_windows_access_denied_hresult_remains_permission_denied(code: int) -> None:
    assert worker().failure_code(ctypes.WinError(code)) == "PERMISSION_DENIED"


@pytest.mark.skipif(os.name != "nt", reason="Windows DbgHelp")
def test_dump_close_failure_removes_its_exclusive_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = worker()
    target = ready_target(tmp_path)
    path = tmp_path / "close-failure.dmp"
    original = Path.open

    @contextmanager
    def fail_on_close(output_path, *args, **kwargs):
        with original(output_path, *args, **kwargs) as stream:
            yield stream
        raise OSError("Injected native file close failure")

    monkeypatch.setattr(Path, "open", fail_on_close)
    try:
        with pytest.raises(OSError, match="Injected native file close failure"):
            module.collect(
                target.pid, psutil.Process(target.pid).create_time(), "mini", path, 64 * 1024**2
            )
        assert not path.exists() and target.poll() is None
    finally:
        target.terminate()
        target.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows DbgHelp")
@pytest.mark.parametrize("mode", ["mini", "full"])
def test_real_dump_is_hashed_has_target_pid_and_contains_owned_memory(
    tmp_path: Path, mode: str
) -> None:
    module = worker()
    target = ready_target(tmp_path)
    try:
        identity = psutil.Process(target.pid).create_time()
        path = tmp_path / "owned.dmp"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                module.__file__,
                "--pid",
                str(target.pid),
                "--create-time",
                str(identity),
                "--mode",
                mode,
                "--max-bytes",
                str(64 * 1024**2),
                "--output",
                str(path),
            ],
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        receipt = json.loads(result.stdout)
        assert result.returncode == 0, receipt
        content = path.read_bytes()
        assert content[:4] == b"MDMP" and len(content) <= 64 * 1024**2
        assert receipt["status"] == "SUCCEEDED" and receipt["target_pid"] == target.pid
        assert receipt["target_create_time"] == pytest.approx(identity, abs=1e-6)
        assert receipt["sha256"] == hashlib.sha256(content).hexdigest()
        assert receipt["bytes"] == len(content) and receipt["process_handle_closed"] is True
        assert bool(struct.unpack_from("<Q", content, 24)[0] & 2) == (mode == "full")
        if mode == "full":
            assert b"RACP_OWNED_DUMP_TEST" in content
        assert target.poll() is None, "Dump must not terminate or own the target"
    finally:
        target.terminate()
        target.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows DbgHelp")
def test_failed_identity_does_not_delete_preexisting_output(tmp_path: Path) -> None:
    module = worker()
    path = tmp_path / "existing.dmp"
    path.write_bytes(b"existing-owned-by-someone-else")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            module.__file__,
            "--pid",
            str(os.getpid()),
            "--create-time",
            "1",
            "--mode",
            "mini",
            "--max-bytes",
            "4096",
            "--output",
            str(path),
        ],
        capture_output=True,
        timeout=5,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert path.read_bytes() == b"existing-owned-by-someone-else"


@pytest.mark.skipif(os.name != "nt", reason="Windows DbgHelp")
@pytest.mark.parametrize("failure", ["budget", "identity"])
def test_real_dump_failure_removes_partial_bytes_and_preserves_target(
    tmp_path: Path, failure: str
) -> None:
    module = worker()
    target = ready_target(tmp_path)
    try:
        identity = psutil.Process(target.pid).create_time()
        path = tmp_path / "partial.dmp"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                module.__file__,
                "--pid",
                str(target.pid),
                "--create-time",
                str(identity + (1 if failure == "identity" else 0)),
                "--mode",
                "full",
                "--max-bytes",
                "4096",
                "--output",
                str(path),
            ],
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        receipt = json.loads(result.stdout)
        assert result.returncode != 0 and receipt["status"] == "FAILED"
        assert receipt["code"] == (
            "RESOURCE_EXHAUSTED" if failure == "budget" else "PRECONDITION_FAILED"
        )
        assert not path.exists() and target.poll() is None
    finally:
        target.terminate()
        target.wait(timeout=5)
