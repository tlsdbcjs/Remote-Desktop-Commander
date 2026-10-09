"""Existing Gateway APIs drive Rust providers in a separate Agent process."""

import asyncio
import hashlib
import os

import pytest
from racp_sdk.security import SecretStore

from .support import ROOT, bridge

pytestmark = pytest.mark.asyncio


async def online(live):
    await bridge(live["executable"], live["state"], "start")
    for _ in range(200):
        device = (await live["http"].get("/api/v1/devices/" + live["device_id"])).json()
        if device["info"].get("status") == "ONLINE":
            return
        await asyncio.sleep(0.05)
    raise AssertionError("Rust Agent did not connect")


async def execute(live, operation, payload, **extra):
    response = await live["http"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": operation,
            "payload": payload,
            "execution_profile_id": "trusted_personal",
            "idempotency_key": extra.pop("key", "rust-" + operation),
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_native_shell_and_managed_process_control(rust_live):
    live = rust_live
    fixture = (
        ROOT
        / "target/debug/examples"
        / ("process_fixture.exe" if os.name == "nt" else "process_fixture")
    )
    assert fixture.exists(), "Build Rust runtime examples before integration tests"
    await online(live)
    result = await execute(live, "shell.exec", {"argv": [str(fixture), "echo"]})
    assert result["state"] == "SUCCEEDED", result
    assert result["result"]["exit_code"] == 7
    assert "native 한글" in result["result"]["stdout"]
    assert str(live["workspace"]) in result["result"]["stdout"]
    assert "native stderr" in result["result"]["stderr"]
    assert result["result"]["cleanup_status"] == "complete"
    started = await execute(live, "process.spawn", {"argv": [str(fixture), "sleep"]})
    assert started["state"] == "SUCCEEDED", started
    target = {key: started["result"][key] for key in ["pid", "create_time", "agent_boot_id"]}
    wrong = {**target, "create_time": target["create_time"] + 1, "force": True}
    rejected = await execute(live, "process.terminate", wrong, key="wrong-birth")
    assert rejected["error"]["code"] == "PRECONDITION_FAILED"
    killed = await execute(live, "process.terminate", {**target, "force": True}, key="kill-owned")
    assert killed["state"] == "SUCCEEDED", killed
    assert killed["result"]["cleanup_status"] == "complete"
    assert killed["result"]["method"] == "owned_tree_kill"


async def test_file_execution_replay_cas_and_no_escape(rust_live):
    live = rust_live
    await online(live)
    result = await execute(live, "filesystem.write", {"path": "한글.txt", "content": "first"})
    assert result["state"] == "SUCCEEDED", result
    replay = await execute(live, "filesystem.write", {"path": "한글.txt", "content": "first"})
    assert replay == result
    assert (live["workspace"] / "한글.txt").read_text() == "first"
    rejected = await execute(
        live,
        "filesystem.write",
        {
            "path": "한글.txt",
            "mode": "replace",
            "overwrite": True,
            "content": "second",
            "expected_sha256": "0" * 64,
        },
        key="reject-cas",
    )
    assert rejected["state"] == "FAILED"
    assert rejected["error"]["code"] == "PRECONDITION_FAILED"
    assert (live["workspace"] / "한글.txt").read_text() == "first"
    read = await execute(live, "filesystem.read", {"path": "한글.txt"})
    assert read["result"]["text"] == "first"
    rejected = await execute(live, "filesystem.read", {"path": "../credential.bin"}, key="escape")
    assert rejected["error"]["code"] == "PATH_ACCESS_DENIED"


async def test_read_only_local_policy_blocks_gateway_mutation(rust_live):
    import json

    live = rust_live
    store = SecretStore(live["state"] / "credential.bin")
    credential = store.load()
    settings = json.loads(credential["agent_settings"])
    settings["profile"] = "read_only"
    credential["agent_settings"] = json.dumps(settings)
    store.save(credential)
    await online(live)
    rejected = await execute(live, "filesystem.write", {"path": "never", "content": "blocked"})
    assert rejected["error"]["code"] == "PERMISSION_DENIED"
    assert not (live["workspace"] / "never").exists()


async def test_100_mib_output_artifact_is_recovered_without_repeat(rust_live):
    live = rust_live
    target = live["workspace"] / "large.bin"
    block = bytes(range(256)) * 4096
    with target.open("wb") as stream:
        for _ in range(100):
            stream.write(block)
    with target.open("rb") as stream:
        expected = hashlib.file_digest(stream, "sha256").hexdigest()
    await online(live)
    result = await execute(live, "filesystem.read", {"path": "large.bin", "binary": True})
    assert result["state"] == "SUCCEEDED", result
    output_id = result["result"]["output_id"]
    operation = result["operation_id"]
    for _ in range(600):
        outputs = (await live["http"].get("/api/v1/operations/" + operation + "/outputs")).json()
        output = next(item for item in outputs["items"] if item["id"] == output_id)
        if output["artifact_id"]:
            break
        await asyncio.sleep(0.1)
    assert output["artifact_id"] and output["sha256"] == expected
    replay = await execute(live, "filesystem.read", {"path": "large.bin", "binary": True})
    assert replay["state"] == "SUCCEEDED" and replay["operation_id"] == operation
    latest = (await live["http"].get("/api/v1/operations/" + operation + "/outputs")).json()
    assert len(latest["items"]) == 1


async def test_binary_input_download_and_atomic_publication(rust_live):
    from racp_sdk.artifacts import ArtifactClient

    live = rust_live
    source = live["workspace"].parent / "input.bin"
    block = bytes(range(256)) * 4096
    with source.open("wb") as stream:
        for _ in range(100):
            stream.write(block)
    owner = live["http"].headers["Authorization"].removeprefix("Bearer ")
    artifact = await ArtifactClient(
        live["gateway"], owner, live["device_id"], ca_file=live["ca"]
    ).upload(source)
    await online(live)
    result = await execute(
        live, "filesystem.write", {"path": "binary.bin", "artifact_id": artifact["id"]}
    )
    assert result["state"] == "SUCCEEDED", result
    assert result["result"]["atomic"]
    with (live["workspace"] / "binary.bin").open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == artifact["sha256"]
    assert not list((live["state"] / "data" / "spool").glob("*.input"))
