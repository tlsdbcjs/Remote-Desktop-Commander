"""Real native reads from a test-owned Windows process, never user processes."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from racp_agent.providers.process import ProcessProvider
from racp_agent.providers.shell import ShellProvider
from racp_domain.models import ExecutionContext, RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.models import new_id
from racp_protocol.provider_models import ProcessMemoryRead
from racp_protocol.registry import validate_payload


def test_memory_access_requires_execution_approval_and_bounded_identity() -> None:
    for operation in ("process.memory_regions", "process.memory_read"):
        assert evaluate(operation, profile_rules("read_only")) == Decision.DENY
        assert evaluate(operation, profile_rules("standard")) == Decision.REQUIRE_APPROVAL
        assert evaluate(operation, profile_rules("trusted_personal")) == Decision.ALLOW
    with pytest.raises(ValueError):
        ProcessMemoryRead(
            pid=3,
            create_time=1,
            agent_boot_id="boot_fixture",
            address="0xffffffffffffffff",
            size_bytes=2,
        )


@pytest.fixture
def native_target(tmp_path: Path):
    if os.name != "nt":
        pytest.skip("Windows process memory")
    script = (
        "import ctypes,json,os,psutil,sys; "
        "data=bytes(range(256))*512; buffer=ctypes.create_string_buffer(data); "
        "print(json.dumps(dict(pid=os.getpid(),create_time=psutil.Process().create_time(),"
        "address=hex(ctypes.addressof(buffer)),size_bytes=len(data))),flush=True); "
        "sys.stdin.readline()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        descriptor = json.loads(process.stdout.readline())
        descriptor["agent_boot_id"] = "boot_memory_fixture"
        provider = ProcessProvider(ShellProvider(tmp_path, tmp_path / "spool"))
        context = ExecutionContext(
            new_id("op"),
            new_id("req"),
            new_id("trace"),
            "dev_memory_fixture",
            "owner_local",
            "boot_memory_fixture",
            5000,
        )
        yield provider, descriptor, context
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


async def test_native_regions_small_read_and_large_artifact(native_target) -> None:
    provider, target, context = native_target
    identity = {key: target[key] for key in ("pid", "create_time", "agent_boot_id")}
    regions = await provider.execute(
        "process.memory_regions",
        validate_payload(
            "process.memory_regions",
            {
                **identity,
                "start_address": target["address"],
                "limit": 1,
            },
        ),
        context,
    )
    region = regions["result"]["items"][0]
    assert region["readable"] and int(region["base_address"], 16) <= int(target["address"], 16)
    small = await provider.execute(
        "process.memory_read",
        validate_payload(
            "process.memory_read",
            {
                **identity,
                "address": target["address"],
                "size_bytes": 64,
            },
        ),
        replace(context, operation_id=new_id("op")),
    )
    assert bytes.fromhex(small["result"]["bytes_hex"]) == bytes(range(64))
    large = await provider.execute(
        "process.memory_read",
        validate_payload("process.memory_read", target),
        replace(context, operation_id=new_id("op")),
    )
    raw = await asyncio.to_thread(Path(large["result"]["spool_path"]).read_bytes)
    assert raw == bytes(range(256)) * 512
    assert large["result"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert large["result"]["atomic_snapshot"] is False


async def test_stale_identity_and_inaccessible_read_leave_no_output(native_target) -> None:
    provider, target, context = native_target
    with pytest.raises(RACPError) as error:
        await provider.execute(
            "process.memory_read", {**target, "create_time": target["create_time"] + 1}, context
        )
    assert error.value.error.code == "PRECONDITION_FAILED"
    with pytest.raises(RACPError) as error:
        await provider.execute(
            "process.memory_read", {**target, "address": "0x0", "size_bytes": 16384}, context
        )
    assert error.value.error.code == "MEMORY_UNAVAILABLE"
    assert list(provider.shell.spool.iterdir()) == []


async def test_cancelled_collection_cleans_partial_and_keeps_target_alive(
    native_target, monkeypatch
) -> None:
    import psutil
    import racp_agent.providers.process_memory as memory

    provider, target, context = native_target
    paused, release = threading.Event(), threading.Event()
    original = memory.WindowsProcessMemory.read

    def held_read(instance, address, size):
        raw = original(instance, address, size)
        paused.set()
        assert release.wait(3)
        return raw

    monkeypatch.setattr(memory.WindowsProcessMemory, "read", held_read)
    task = asyncio.create_task(
        provider.execute("process.memory_read", {**target, "size_bytes": 8192}, context)
    )
    try:
        assert await asyncio.to_thread(paused.wait, 3)
        task.cancel()
        await asyncio.sleep(0)  # Deliver cancellation while the native worker is paused.
        release.set()
        with pytest.raises(RACPError) as error:
            await task
        assert error.value.error.code == "CANCELLED"
        assert list(provider.shell.spool.iterdir()) == []
        assert psutil.pid_exists(target["pid"])
    finally:
        release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)
