import asyncio
import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest
from racp_domain.models import ExecutionContext, RACPError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows dump containment")


def provider_type():
    assert importlib.util.find_spec("racp_agent.providers.process_dump"), "Dump Provider missing"
    return importlib.import_module("racp_agent.providers.process_dump").ProcessDumpProvider


def context(timeout: int = 10000) -> ExecutionContext:
    return ExecutionContext(
        "op_dump", "req_dump", "0" * 32, "dev_owned", "owner", "boot_owned", timeout, "default"
    )


@pytest.fixture
def target():
    owned = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time;time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        yield {
            "pid": owned.pid,
            "create_time": psutil.Process(owned.pid).create_time(),
            "agent_boot_id": "boot_owned",
            "mode": "mini",
            "max_bytes": 64 * 1024**2,
        }
    finally:
        owned.terminate()
        owned.wait(timeout=5)


async def test_real_provider_dump_does_not_need_exec_and_releases_only_owned_spool(
    tmp_path: Path, target: dict
) -> None:
    provider = provider_type()(tmp_path / "spool")
    outcome = await provider.execute(target, context(), lambda: None)
    path = Path(outcome["result"]["spool_path"])
    assert (await asyncio.to_thread(path.read_bytes))[:4] == b"MDMP"
    assert outcome["state"] == "SUCCEEDED" and outcome["result"]["target_pid"] == target["pid"]
    assert outcome["result"]["cleanup_status"] == "complete" and psutil.pid_exists(target["pid"])
    assert not provider.protected_pids()
    provider.release("op_dump", preserve=False)
    assert not await asyncio.to_thread(path.exists)


@pytest.mark.parametrize("failure", ["identity", "boot", "protected"])
async def test_dump_refuses_wrong_identity_or_protected_target_before_worker(
    tmp_path: Path, target: dict, failure: str
) -> None:
    provider = provider_type()(tmp_path / "spool")
    if failure == "identity":
        target["create_time"] += 1
    elif failure == "boot":
        target["agent_boot_id"] = "boot_old"
    else:
        provider.extra_protected_pids = lambda: {target["pid"]}
    with pytest.raises(RACPError) as raised:
        await provider.execute(target, context(), lambda: None)
    assert raised.value.error.code == (
        "PERMISSION_DENIED" if failure == "protected" else "PRECONDITION_FAILED"
    )
    assert not provider.pending and not list(provider.spool.glob("*.dmp"))


@pytest.mark.parametrize("reason", ["cancel", "timeout", "revoke"])
async def test_dump_failure_terminates_worker_preserves_target_and_removes_partial(
    tmp_path: Path, target: dict, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    from racp_agent.plugins.process import ContainedCommand

    provider = provider_type()(tmp_path / "spool")
    script = tmp_path / "owned_dump_fixture.py"
    script.write_text(
        "import sys,time;from pathlib import Path\n"
        "Path(sys.argv[1]).write_bytes(b'MDMP')\ntime.sleep(60)\n"
    )
    monkeypatch.setattr(
        provider,
        "command",
        lambda data, path: ContainedCommand(
            [sys.executable, "-I", str(script), str(path)], str(tmp_path)
        ),
    )
    revoked = False

    def gate():
        if revoked:
            raise RACPError("PERMISSION_DENIED", "Fixture revoked")

    task = asyncio.create_task(
        provider.execute(target, context(1200 if reason == "timeout" else 10000), gate)
    )
    pids = set()
    try:
        async with asyncio.timeout(8):
            while not task.done():
                pids.update(provider.protected_pids())
                if reason != "timeout" and list(provider.spool.glob("*.dmp")):
                    break
                await asyncio.sleep(0.02)
        if reason == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 8)
        else:
            revoked = reason == "revoke"
            with pytest.raises(RACPError) as raised:
                await asyncio.wait_for(task, 8)
            assert raised.value.error.code == (
                "TIMEOUT" if reason == "timeout" else "PERMISSION_DENIED"
            )
        assert not provider.pending and not list(provider.spool.glob("*.dmp"))
        assert not provider.protected_pids() and all(not psutil.pid_exists(pid) for pid in pids)
        assert psutil.pid_exists(target["pid"])
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
