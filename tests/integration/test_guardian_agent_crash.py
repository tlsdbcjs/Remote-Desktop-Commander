import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from racp_agent.broker.identity import process_identity
from racp_agent.broker.peer_process import PeerProcess


@pytest.mark.skipif(os.name != "nt", reason="Windows Agent Job/Guardian lifetime")
async def test_agent_crash_keeps_guardian_until_cleanup_and_removes_ephemeral_task(
    tmp_path: Path,
) -> None:
    actor = process_identity(os.getpid())
    if actor.session == 0:
        pytest.skip("requires a logged-on user session")
    await assert_agent_crash(tmp_path, None)


async def assert_agent_crash(tmp_path: Path, window_pid: int | None, desktop: Any = None) -> None:
    import win32api
    import win32event

    script = Path(__file__).parents[1] / "fixtures/agent_guard_lifetime.py"
    argv = [sys.executable, "-I", str(script), "--root", str(tmp_path / "agent")]
    if window_pid is not None:
        argv.extend(["--window-pid", str(window_pid)])
    child = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    guardian = None
    try:
        assert child.stdout is not None
        raw = await asyncio.wait_for(child.stdout.readline(), 10)
        if not raw:
            assert child.stderr is not None
            pytest.fail((await child.stderr.read()).decode(errors="replace"))
        ready = json.loads(raw)
        agent = process_identity(ready["agent_pid"])
        assert (
            agent.created == ready["agent_created"]
            and agent.sid == process_identity(os.getpid()).sid
        )
        guardian_identity = process_identity(ready["guardian_pid"])
        assert (
            guardian_identity.created == ready["guardian_created"]
            and guardian_identity.sid == agent.sid
        )
        guardian = PeerProcess(
            guardian_identity.pid, win32api.OpenProcess(0x101000, False, guardian_identity.pid)
        )
        if desktop is not None:
            assert (
                desktop.user.GetAsyncKeyState(0xA2) & 0x8000
                and desktop.user.GetAsyncKeyState(1) & 0x8000
            )
        handle = win32api.OpenProcess(0x101001, False, agent.pid)
        try:
            assert process_identity(agent.pid) == agent
            win32api.TerminateProcess(handle, 1)
            assert await asyncio.to_thread(win32event.WaitForSingleObject, handle, 5000) == 0
        finally:
            handle.Close()
        assert await asyncio.wait_for(guardian.wait(), 7) == 0
        if desktop is not None:
            assert not desktop.user.GetAsyncKeyState(0xA2) & 0x8000
            assert not desktop.user.GetAsyncKeyState(1) & 0x8000
            assert desktop.input_lease.gate.ready()
        if ready["cleanup_directory"] is not None:
            assert not await asyncio.to_thread(Path(ready["cleanup_directory"]).exists)
        await asyncio.wait_for(child.wait(), 5)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        if guardian is not None:
            guardian.close()
