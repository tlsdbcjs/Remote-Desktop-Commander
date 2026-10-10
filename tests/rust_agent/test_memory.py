"""Native Windows memory observations from an owned Rust fixture through the real Gateway."""

import asyncio
import hashlib
import json
import os
import subprocess

import pytest
import pytest_asyncio
from racp_sdk.security import SecretStore

from .support import ROOT, bridge
from .test_browser import verified_output
from .test_execution import execute, online

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.name != "nt", reason="Windows x64 native memory"),
]


@pytest_asyncio.fixture
async def owned_memory_target():
    child = await asyncio.create_subprocess_exec(
        str(ROOT / "target/debug/examples/process_fixture.exe"),
        "memory",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        data = json.loads(await asyncio.wait_for(child.stdout.readline(), 5))
        yield data
    finally:
        try:
            await asyncio.wait_for(child.communicate(b"exit\n"), 5)
        except TimeoutError:
            child.kill()
            await child.wait()


async def target(live, data):
    inspected = await execute(live, "process.inspect", {"pid": data["pid"]})
    assert inspected["state"] == "SUCCEEDED", inspected
    return {key: inspected["result"][key] for key in ("pid", "create_time", "agent_boot_id")}


async def test_native_memory_bytes_artifact_and_identity_fences(rust_live, owned_memory_target):
    live, data = rust_live, owned_memory_target
    await online(live)
    identity = await target(live, data)
    region = await execute(
        live, "process.memory_regions", {**identity, "start_address": data["address"], "limit": 1}
    )
    assert region["state"] == "SUCCEEDED", region
    assert region["result"]["items"][0]["readable"]
    payload = {**identity, "address": data["address"], "size_bytes": 64}
    observed = await execute(live, "process.memory_read", payload)
    assert observed["state"] == "SUCCEEDED", observed
    assert bytes.fromhex(observed["result"]["bytes_hex"]) == bytes(range(64))
    assert observed["result"]["sha256"] == hashlib.sha256(bytes(range(64))).hexdigest()
    assert observed["result"]["consistency"] == "live_process_observation"
    assert observed["result"]["atomic_snapshot"] is False
    assert await execute(live, "process.memory_read", payload) == observed
    large = await execute(
        live,
        "process.memory_read",
        {**payload, "size_bytes": data["size_bytes"]},
        key="memory-large",
    )
    assert large["state"] == "SUCCEEDED", large
    assert await verified_output(live, large) == bytes(range(256)) * 512
    bad = await execute(
        live,
        "process.memory_read",
        {**payload, "create_time": identity["create_time"] + 1},
        key="memory-wrong-birth",
    )
    assert bad["error"]["code"] == "PRECONDITION_FAILED"
    bad = await execute(
        live,
        "process.memory_read",
        {**payload, "address": "0x1", "size_bytes": 8192},
        key="memory-inaccessible",
    )
    assert bad["error"]["code"] == "MEMORY_UNAVAILABLE"
    assert not (live["state"] / "data/spool" / (bad["operation_id"] + ".process-memory")).exists()
    status = await bridge(live["executable"], live["state"], "status")
    protected = await target(live, {"pid": status["pid"]})
    bad = await execute(
        live,
        "process.memory_read",
        {**protected, "address": "0x1", "size_bytes": 64},
        key="memory-protected-agent",
    )
    assert bad["error"]["code"] == "PERMISSION_DENIED"


async def test_read_only_local_policy_denies_native_memory(rust_live, owned_memory_target):
    live, data = rust_live, owned_memory_target
    secret = SecretStore(live["state"] / "credential.bin")
    saved = secret.load()
    settings = json.loads(saved["agent_settings"])
    settings["profile"] = "read_only"
    secret.save({**saved, "agent_settings": json.dumps(settings)})
    await online(live)
    identity = await target(live, data)
    denied = await execute(
        live, "process.memory_read", {**identity, "address": data["address"], "size_bytes": 64}
    )
    assert denied["state"] == "FAILED" and denied["error"]["code"] == "PERMISSION_DENIED"
    assert denied["result"] is None
