"""Real Rust transfers against the existing Gateway; secrets travel only over stdin."""

import asyncio
import hashlib
import json
import os
import subprocess

import pytest
from racp_sdk.security import SecretStore

from .support import ROOT, operation

pytestmark = pytest.mark.asyncio


async def fixture(live, **arguments):
    executable = (
        ROOT
        / "target"
        / "debug"
        / "examples"
        / ("artifact_fixture.exe" if os.name == "nt" else "artifact_fixture")
    )
    assert executable.exists(), "Build the Rust artifact fixture before testing"
    process = await asyncio.create_subprocess_exec(
        str(executable), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    credential = SecretStore(live["state"] / "credential.bin").load()["credential"]
    output, error = await asyncio.wait_for(
        process.communicate(
            json.dumps(
                {
                    "gateway": live["gateway"],
                    "credential": credential,
                    "device_id": live["device_id"],
                    "ca": str(live["ca"]),
                    **arguments,
                }
            ).encode()
        ),
        120,
    )
    assert not error
    result = json.loads(output)
    assert result["ok"], result
    return result["result"]


async def test_100_mib_resume_upload_download_and_checksum(rust_live, tmp_path):
    live = rust_live
    spool = tmp_path / "transfer-spool"
    spool.mkdir()
    source = spool / "op_transfer.bin"
    block = bytes(range(256)) * 4096
    with source.open("wb") as stream:
        for _ in range(100):
            stream.write(block)
    size = source.stat().st_size
    sha256 = hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
    op = operation(live, "filesystem.read", {"path": "fixture.bin"}, "transfer")
    # Simulate a disconnected upload after the Gateway committed its first chunk.
    transfer = (
        await live["http"].post(
            "/api/v1/artifact-transfers",
            json={
                "device_id": live["device_id"],
                "operation_id": op,
                "size_bytes": size,
                "sha256": sha256,
            },
        )
    ).json()
    prefix = block * 4
    response = await live["http"].put(
        "/api/v1/artifact-transfers/" + transfer["id"] + "/content",
        content=prefix,
        headers={
            "Authorization": "Bearer " + transfer["credential"],
            "Content-Range": f"bytes 0-{len(prefix) - 1}/{size}",
            "X-Chunk-SHA256": hashlib.sha256(prefix).hexdigest(),
        },
    )
    assert response.status_code == 200
    artifact = await fixture(
        live,
        action="upload",
        path=str(source),
        operation_id=op,
        transfer_id=transfer["id"],
        db=str(tmp_path / "fixture.db"),
    )
    assert artifact["sha256"] == sha256 and artifact["size_bytes"] == size
    op = operation(
        live,
        "filesystem.write",
        {"path": "destination.bin", "artifact_id": artifact["id"]},
        "receive",
    )
    destination = spool / "received.bin"
    destination.write_bytes(prefix)
    result = await fixture(
        live, action="download", path=str(destination), operation_id=op, artifact_id=artifact["id"]
    )
    with destination.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    assert actual == sha256 and result["size_bytes"] == size
    corrupt = spool / "corrupt.bin"
    corrupt.write_bytes(b"x" * size)
    with pytest.raises(AssertionError, match="CHECKSUM_MISMATCH"):
        await fixture(
            live, action="download", path=str(corrupt), operation_id=op, artifact_id=artifact["id"]
        )
