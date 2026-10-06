import asyncio
import errno
import hashlib
import sys
import time
from typing import Any

import httpx
import pytest
from racp_domain.models import RACPError
from racp_protocol.artifacts import CHUNK_BYTES
from racp_sdk.artifacts import ArtifactClient, file_digest
from starlette.requests import Request


async def create(
    live: dict[str, Any], data: bytes, *, expected: str | None = None
) -> dict[str, Any]:
    response = await live["client"].post(
        "/api/v1/artifact-transfers",
        json={
            "device_id": live["device_id"],
            "size_bytes": len(data),
            "sha256": expected or hashlib.sha256(data).hexdigest(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def chunk(
    live: dict[str, Any],
    transfer: dict[str, Any],
    data: bytes,
    offset: int = 0,
    *,
    credential: str | None = None,
    checksum: str | None = None,
) -> httpx.Response:
    return await live["client"].put(
        "/api/v1/artifact-transfers/" + transfer["id"] + "/content",
        content=data,
        headers={
            "Authorization": "Bearer " + (credential or transfer["credential"]),
            "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{transfer['size_bytes']}",
            "X-Chunk-SHA256": checksum or hashlib.sha256(data).hexdigest(),
        },
    )


async def complete(live: dict[str, Any], transfer: dict[str, Any]) -> httpx.Response:
    return await live["client"].post(
        "/api/v1/artifact-transfers/" + transfer["id"] + "/complete",
        headers={"Authorization": "Bearer " + transfer["credential"]},
        json={"size_bytes": transfer["size_bytes"], "sha256": transfer["sha256"]},
    )


async def test_transfer_scope_hash_replay_and_readiness(live: dict[str, Any]) -> None:
    data = b"binary\0payload\xff"
    transfer = await create(live, data)
    other = await create(live, data)
    client = live["client"]
    not_ready = await client.get("/api/v1/artifacts/" + transfer["artifact_id"] + "/content")
    assert not_ready.status_code == 409
    assert (await chunk(live, transfer, data, credential=other["credential"])).status_code == 401
    assert (await chunk(live, transfer, data, checksum="0" * 64)).status_code == 412
    good = await chunk(live, transfer, data)
    assert good.status_code == 200 and good.json()["committed_bytes"] == str(len(data))
    replay = await chunk(live, transfer, data)
    assert replay.json() == good.json()
    changed = await chunk(live, transfer, b"x" * len(data))
    assert changed.status_code == 409
    sealed = await complete(live, transfer)
    assert sealed.status_code == 200 and sealed.json()["state"] == "READY"
    assert (await complete(live, transfer)).json() == sealed.json()
    status = (await client.get("/api/v1/artifact-transfers/" + transfer["id"])).json()
    assert "credential" not in status and "credential_digest" not in status


async def test_bad_complete_hash_does_not_publish_or_damage_ready_blob(
    live: dict[str, Any],
) -> None:
    data = b"valid fixture"
    valid = await create(live, data)
    await chunk(live, valid, data)
    sealed = (await complete(live, valid)).json()
    invalid = await create(live, b"different fixture", expected=hashlib.sha256(data).hexdigest())
    await chunk(live, invalid, b"different fixture")
    failed = await complete(live, invalid)
    assert failed.status_code == 412
    metadata = (await live["client"].get("/api/v1/artifacts/" + invalid["artifact_id"])).json()
    assert metadata["state"] == "FAILED"
    intact = await live["client"].get("/api/v1/artifacts/" + sealed["id"] + "/content")
    assert intact.content == data


async def test_range_etag_token_expiry_renewal_and_quota_reservation(live: dict[str, Any]) -> None:
    data = bytes(range(256)) * 32
    transfer = await create(live, data)
    manager = live["app"].state.artifacts
    manager.store.db.execute(
        "UPDATE artifact_transfers SET credential_expires=? WHERE id=?",
        (time.time() - 1, transfer["id"]),
    )
    assert (await chunk(live, transfer, data)).status_code == 410
    renewed = await live["client"].post(
        "/api/v1/artifact-transfers/" + transfer["id"] + "/authorize"
    )
    transfer = renewed.json()
    assert (await chunk(live, transfer, data)).status_code == 200
    artifact = (await complete(live, transfer)).json()
    url = "/api/v1/artifacts/" + artifact["id"] + "/content"
    partial = await live["client"].get(url, headers={"Range": "bytes=3-100"})
    assert partial.status_code == 206 and partial.content == data[3:101]
    assert partial.headers["etag"] == '"' + artifact["sha256"] + '"'
    assert partial.headers["content-range"] == f"bytes 3-100/{len(data)}"
    suffix = await live["client"].get(url, headers={"Range": "bytes=-10"})
    assert suffix.content == data[-10:]
    assert (await live["client"].get(url, headers={"Range": "bytes=999999-"})).status_code == 416
    full = await live["client"].get(url, headers={"Range": "bytes=3-100", "If-Range": '"wrong"'})
    assert full.status_code == 200 and full.content == data
    manager.quota_bytes = len(data) + 10
    reserved = await create(live, b"1234567890")
    denied = await live["client"].post(
        "/api/v1/artifact-transfers",
        json={
            "device_id": live["device_id"],
            "size_bytes": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        },
    )
    assert denied.status_code == 429
    assert reserved["state"] == "UPLOADING"


async def test_binary_write_downloads_verified_input_and_preserves_target_on_conflict(
    live: dict[str, Any],
) -> None:
    source = live["workspace"].parent / "source.bin"
    raw = bytes(range(256)) * 2048
    await asyncio.to_thread(source.write_bytes, raw)
    artifact = await ArtifactClient(live["url"], live["owner"], live["device_id"]).upload(source)
    request = {
        "device_id": live["device_id"],
        "operation": "filesystem.write",
        "idempotency_key": "binary-write-once",
        "execution_profile_id": "trusted_personal",
        "payload": {"path": "received.bin", "artifact_id": artifact["id"]},
    }
    first = await live["client"].post("/api/v1/operations", json=request)
    assert first.status_code == 200 and first.json()["state"] == "SUCCEEDED", first.text
    target = live["workspace"] / "received.bin"
    assert await asyncio.to_thread(target.read_bytes) == raw
    replay = await live["client"].post("/api/v1/operations", json=request)
    assert replay.json() == first.json()
    request["idempotency_key"] = "conflicting-replace"
    request["payload"].update({"mode": "replace", "overwrite": True, "expected_sha256": "0" * 64})
    failed = await live["client"].post("/api/v1/operations", json=request)
    assert failed.json()["error"]["code"] == "PRECONDITION_FAILED"
    assert await asyncio.to_thread(target.read_bytes) == raw


async def test_art_01_100_mib_disconnect_resume_and_binary_write(live: dict[str, Any]) -> None:
    source = live["workspace"].parent / "100-mib.bin"
    block = bytes(range(256)) * 4096

    def fixture() -> None:
        with source.open("wb") as stream:
            for _ in range(100):
                stream.write(block)

    await asyncio.to_thread(fixture)
    size, sha256 = await asyncio.to_thread(file_digest, source)
    response = await live["client"].post(
        "/api/v1/artifact-transfers",
        json={"device_id": live["device_id"], "size_bytes": size, "sha256": sha256},
    )
    transfer = response.json()
    prefix = block * 4
    assert (await chunk(live, transfer, prefix)).status_code == 200

    async def broken_body() -> Any:
        yield prefix[: len(prefix) // 2]
        raise RuntimeError("injected transport disconnect")

    with pytest.raises(RuntimeError, match="injected transport disconnect"):
        async with httpx.AsyncClient(base_url=live["url"], timeout=10) as client:
            await client.put(
                "/api/v1/artifact-transfers/" + transfer["id"] + "/content",
                content=broken_body(),
                headers={
                    "Authorization": "Bearer " + transfer["credential"],
                    "Content-Range": f"bytes {CHUNK_BYTES}-{2 * CHUNK_BYTES - 1}/{size}",
                    "X-Chunk-SHA256": hashlib.sha256(prefix).hexdigest(),
                },
            )
    status = (await live["client"].get("/api/v1/artifact-transfers/" + transfer["id"])).json()
    assert int(status["committed_bytes"]) == CHUNK_BYTES
    artifact = await ArtifactClient(live["url"], live["owner"], live["device_id"]).upload(
        source, transfer_id=transfer["id"]
    )
    assert (
        artifact["state"] == "READY"
        and artifact["sha256"] == sha256
        and artifact["size_bytes"] == size
    )
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "filesystem.write",
            "payload": {"path": "100-mib-target.bin", "artifact_id": artifact["id"]},
            "idempotency_key": "100-mib-write",
            "execution_profile_id": "trusted_personal",
            "timeout_ms": 120000,
            "execution_mode": "job",
        },
    )
    operation_id = response.json()["operation_id"]
    for _ in range(600):
        result = (await live["client"].get("/api/v1/operations/" + operation_id)).json()
        if result["state"] in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "UNKNOWN"}:
            break
        await asyncio.sleep(0.05)
    assert result["state"] == "SUCCEEDED", result
    assert await asyncio.to_thread(file_digest, live["workspace"] / "100-mib-target.bin") == (
        size,
        sha256,
    )


async def test_state_01_gateway_restart_recovers_committed_transfer_and_result(
    live: dict[str, Any],
) -> None:
    data = b"prefix" + b"suffix"
    transfer = await create(live, data)
    assert (await chunk(live, transfer, data[:6])).status_code == 200
    partial = live["app"].state.artifacts.partial / transfer["id"]
    with partial.open("ab") as stream:
        stream.write(b"uncommitted crash tail")
    request = {
        "device_id": live["device_id"],
        "operation": "filesystem.write",
        "idempotency_key": "restart-result",
        "execution_profile_id": "trusted_personal",
        "payload": {"path": "restart.txt", "content": "once"},
    }
    result = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert result["state"] == "SUCCEEDED"
    await live["restart_gateway"]()
    assert partial.read_bytes() == data[:6]
    status = (await live["client"].get("/api/v1/artifact-transfers/" + transfer["id"])).json()
    assert status["state"] == "UPLOADING" and status["committed_bytes"] == "6"
    assert (await chunk(live, transfer, data[6:], 6)).status_code == 200
    assert (await complete(live, transfer)).json()["state"] == "READY"
    replay = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert replay == result
    assert (live["workspace"] / "restart.txt").read_text() == "once"


async def test_art_02_disk_full_incomplete_cleanup_and_gc_reader_pin(
    live: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"protected blob" * 10000
    transfer = await create(live, data)
    await chunk(live, transfer, data)
    artifact = (await complete(live, transfer)).json()
    manager = live["app"].state.artifacts
    failing = await create(live, b"disk-full")

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "injected disk full")

    with monkeypatch.context() as patch:
        patch.setattr("racp_gateway.artifact_transfers.os.fsync", disk_full)
        failed = await chunk(live, failing, b"disk-full")
        assert failed.status_code == 429
    assert manager.transfer(failing["id"])["state"] == "FAILED"
    assert not (manager.partial / failing["id"]).exists()
    stale = await create(live, b"incomplete")
    await chunk(live, stale, b"inc")
    manager.store.db.execute(
        "UPDATE artifact_transfers SET updated=? WHERE id=?",
        (time.time() - 3601, stale["id"]),
    )
    response = manager.response(
        artifact["id"], "owner_local", Request({"type": "http", "headers": []})
    )
    stream = response.body_iterator
    prefix = await anext(stream)
    manager.store.db.execute(
        "UPDATE artifacts SET expires=? WHERE id=?",
        (time.time() - 1, artifact["id"]),
    )
    collected = await manager.collect()
    assert collected["incomplete_cleaned"] == 1
    assert not (manager.partial / stale["id"]).exists()
    assert manager.get(artifact["id"], "owner_local")["state"] == "READY"
    output = bytearray(prefix)
    async for block in stream:
        output.extend(block)
    assert bytes(output) == data
    assert not manager.readers
    assert (await manager.collect())["deleted"] == 1
    assert not (manager.root / artifact["sha256"]).exists()
    expired = await live["client"].get("/api/v1/artifacts/" + artifact["id"] + "/content")
    assert expired.status_code == 410


async def test_output_upload_failure_preserves_completed_provider_result(
    live: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exhausted(*args: Any, **kwargs: Any) -> str:
        raise RACPError("RESOURCE_EXHAUSTED", "artifact reservation exceeds quota")

    monkeypatch.setattr(live["agent"], "_upload", exhausted)
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "shell.exec",
            "payload": {"argv": [sys.executable, "-c", "print('x' * 100000)"]},
            "idempotency_key": "output-quota",
            "execution_profile_id": "trusted_personal",
        },
    )
    result = response.json()
    assert result["state"] == "SUCCEEDED", result
    assert result["result"]["artifact_upload_status"] == "pending"
    assert result["result"]["artifact_upload_error"]["code"] == "RESOURCE_EXHAUSTED"
    assert list(live["agent"].spool.glob("*"))
