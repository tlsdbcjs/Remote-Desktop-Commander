"""Native worker shutdown must permit EOF cleanup while retaining bounded containment."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from racp_agent.providers.browser import BrowserProvider, BrowserSession
from racp_domain.models import ExecutionContext

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native browser worker containment")


@pytest.mark.parametrize("cooperative", [True, False])
async def test_owned_worker_eof_cleanup_and_unresponsive_fallback(
    tmp_path: Path, cooperative: bool
) -> None:
    import win32api
    import win32con
    import win32job

    marker = tmp_path / "eof-cleanup"
    after_ready = (
        f"sys.stdin.buffer.read(); Path({str(marker)!r}).write_text('cleaned')"
        if cooperative
        else "time.sleep(30)"
    )
    source = "import sys,time;from pathlib import Path;print('ready',flush=True);" + after_ready
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        source,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    handle = win32api.OpenProcess(
        win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid
    )
    try:
        win32job.AssignProcessToJobObject(job, handle)
    finally:
        handle.Close()
    context = ExecutionContext(
        operation_id="op_close",
        request_id="req_close",
        trace_id="trace_close",
        device_id="dev_close",
        principal_id="owner_local",
        agent_boot_id="boot_close",
        timeout_ms=30000,
    )
    session = BrowserSession("browser_close", context, process, job=job)
    provider = BrowserProvider(tmp_path / "spool")
    try:
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stdout.readline(), 10) == b"ready\r\n"
        started = time.monotonic()
        result = await provider.close(session)
        assert time.monotonic() - started < 5.5
        assert result["cleanup_status"] == "complete" and process.returncode is not None
        assert marker.exists() is cooperative
    finally:
        if session.job is not None:
            session.job.Close()
            session.job = None
        if process.returncode is None:
            process.kill()
        await process.wait()
