"""Revocation between upload chunks must stop bytes and completion authorization."""

from pathlib import Path

import httpx
import pytest
from racp_domain.models import RACPError
from racp_protocol.artifacts import CHUNK_BYTES
from racp_sdk.artifacts import ArtifactClient


async def test_upload_rechecks_local_gate_after_chunk_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "owned.bin"
    source.write_bytes(b"x" * (CHUNK_BYTES + 1))
    allowed = True
    uploads = 0
    completed = False
    original = httpx.AsyncClient

    def guard() -> None:
        if not allowed:
            raise RACPError("PERMISSION_DENIED", "local fixture revoked export", layer="agent")

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal allowed, uploads, completed
        if request.url.path.endswith("/complete"):
            completed = True
            return httpx.Response(200, json={"id": "art_should_not_exist"})
        if request.method == "PUT":
            uploads += 1
            assert len(request.content) == CHUNK_BYTES
            allowed = False
            return httpx.Response(200, json={"committed_bytes": str(CHUNK_BYTES)})
        return httpx.Response(
            201,
            json={
                "id": "transfer_fixture",
                "credential": "fixture-transfer-secret",
                "committed_bytes": "0",
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    client = ArtifactClient("https://gateway.example", "fixture-device-secret", "dev_fixture")
    with pytest.raises(RACPError) as raised:
        await client.upload(source, authorization_gate=guard)
    assert raised.value.error.code == "PERMISSION_DENIED"
    assert uploads == 1 and not completed
