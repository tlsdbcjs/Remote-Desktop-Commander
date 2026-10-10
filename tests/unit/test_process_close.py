"""Closing an owned Windows tree must wait for children as well as its gate."""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from racp_agent.providers.process import ProcessProvider
from racp_agent.providers.shell import ShellProvider
from racp_domain.models import ExecutionContext


@pytest.mark.skipif(os.name != "nt", reason="Windows Job asynchronous termination")
async def test_close_waits_for_owned_children_after_gate_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import win32api
    import win32con
    import win32job

    provider = ProcessProvider(ShellProvider(tmp_path, tmp_path / "spool"))
    context = ExecutionContext(
        operation_id="op_close",
        request_id="req_close",
        trace_id="trace_close",
        device_id="dev_close",
        principal_id="owner_local",
        agent_boot_id="boot_close",
        timeout_ms=5000,
    )
    spawned = await provider._spawn(
        {"argv": [sys.executable, "-c", "import time;time.sleep(30)"]}, context
    )
    managed = provider.managed[spawned["handle"]["id"]]
    duplicate = win32api.DuplicateHandle(
        win32api.GetCurrentProcess(),
        managed.job,
        win32api.GetCurrentProcess(),
        0,
        False,
        win32con.DUPLICATE_SAME_ACCESS,
    )
    terminate = win32job.TerminateJobObject
    delayed: asyncio.Task[None] | None = None

    async def child_termination() -> None:
        await asyncio.sleep(0.15)
        terminate(duplicate, 1)

    def begin_termination(job: object, code: int) -> None:
        nonlocal delayed
        # A Job termination can signal the gate before all children finish.
        # Retain a duplicate so CloseHandle cannot mask that ordering.
        managed.process.kill()
        delayed = asyncio.create_task(child_termination())

    monkeypatch.setattr(win32job, "TerminateJobObject", begin_termination)
    try:
        await provider.close(managed)
        assert delayed is not None and delayed.done(), (
            "close returned while the owned child was alive"
        )
        assert (
            win32job.QueryInformationJobObject(
                duplicate, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]
            == 0
        )
    finally:
        terminate(duplicate, 1)
        if delayed is not None:
            await delayed
        duplicate.Close()
        if managed.job is not None:
            managed.job.Close()
            managed.job = None
        await managed.process.wait()
