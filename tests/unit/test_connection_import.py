import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from racp_agent import desktop_control
from racp_agent.connection_import import load_connection
from racp_domain.models import RACPError
from racp_gateway.app import create_app
from racp_gateway.store import GatewayStore
from racp_sdk.connection_file import ConnectionFile
from racp_sdk.security import SecretStore, digest, token
from tls_fixture import certificates


def offer(path: Path, **changes: object) -> tuple[Path, str]:
    value = ConnectionFile.model_validate(
        {
            "gateway": "https://gateway.example",
            "token": token(),
            "expires_at": datetime.now(UTC) + timedelta(minutes=10),
            **changes,
        }
    )
    path.write_text(value.model_dump_json(), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_preview_never_contains_token_and_does_not_create_state(tmp_path: Path) -> None:
    path, fingerprint = offer(tmp_path / "connection.racp")
    value, actual = load_connection(path)
    assert actual == fingerprint
    preview = value.preview(actual)
    assert preview["gateway"] == "https://gateway.example"
    assert value.token not in json.dumps(preview)
    assert "token" not in preview and "ca_pem" not in preview


@pytest.mark.parametrize("invalid", ["oversized", "private-key", "expired", "changed", "extra"])
def test_connection_file_rejects_invalid_or_changed_input(tmp_path: Path, invalid: str) -> None:
    path, fingerprint = offer(tmp_path / "connection.racp")
    if invalid == "oversized":
        path.write_bytes(b"x" * 32769)
    elif invalid == "expired":
        offer(path, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    elif invalid == "changed":
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        raw = json.loads(path.read_text())
        raw["ca_pem" if invalid == "private-key" else "argv"] = "private-key-fixture"
        path.write_text(json.dumps(raw))
    with pytest.raises(RACPError) as raised:
        load_connection(path, fingerprint if invalid == "changed" else None)
    assert raised.value.error.code == {
        "expired": "CONNECTION_FILE_EXPIRED",
        "changed": "CONNECTION_FILE_CHANGED",
    }.get(invalid, "CONNECTION_FILE_INVALID")


async def test_import_persists_ca_without_persisting_offer_token_or_requiring_original_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ca, _, _ = certificates(tmp_path / "tls")
    path, fingerprint = offer(tmp_path / "connection.racp", ca_pem=ca.read_text())
    value, _ = load_connection(path)
    issued_credential = token()
    calls = []
    original = httpx.Client

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json={"device_id": "dev_import_fixture", "credential": issued_credential}
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs)
    )
    state = tmp_path / "state"
    request = desktop_control.ImportConnection(
        action="enroll_connection", path=path, file_sha256=fingerprint, workspace=tmp_path
    )
    result = await desktop_control.execute(request, state)
    assert result["configured"] is True and result["profile"] == "read_only"
    assert calls == [{"token": value.token}]
    saved = SecretStore(state / "credential.bin").load()
    assert value.token not in json.dumps(saved)
    imported = Path(saved["ca_file"])
    assert imported.parent == state and await asyncio.to_thread(imported.read_text) == value.ca_pem
    before = (state / "credential.bin").read_bytes()
    with pytest.raises(RACPError) as raised:
        await desktop_control.execute(request, state)
    assert raised.value.error.code == "CONFLICT" and len(calls) == 1
    assert (state / "credential.bin").read_bytes() == before
    path.unlink()
    ca.unlink()
    assert desktop_control.information(state / "credential.bin")["configured"] is True


async def test_connection_file_issuer_requires_owner_and_exports_only_public_trust(
    tmp_path: Path,
) -> None:
    ca, _, key = certificates(tmp_path / "tls")
    public_ca = await asyncio.to_thread(ca.read_text)
    root = tmp_path / "gateway"
    secret = token()
    store = GatewayStore(root / "gateway.db")
    store.initialize(digest(secret))
    device = store.enroll(store.enrollment("Owned fixture"))
    store.close()
    app = create_app(root, public_origin="https://gateway.example", client_ca_pem=public_ca)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://gateway.example",
        ) as http,
    ):
        payload = {"name": "Connection file fixture", "include_connection_file": True}
        assert (await http.post("/api/v1/enrollment-tokens", json=payload)).status_code == 401
        assert (
            await http.post(
                "/api/v1/enrollment-tokens",
                json=payload,
                headers={"Authorization": "Bearer " + device["credential"]},
            )
        ).status_code == 401
        response = await http.post(
            "/api/v1/enrollment-tokens", json=payload, headers={"Authorization": "Bearer " + secret}
        )
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        result = response.json()
        offer = ConnectionFile.model_validate_json(json.dumps(result["connection_file"]))
        assert offer.token == result["token"] and offer.gateway == "https://gateway.example"
        assert offer.ca_pem == public_ca and secret not in response.text
        assert "PRIVATE KEY" not in response.text
        accepted = await http.post("/agent/v1/enroll", json={"token": offer.token})
        assert accepted.status_code == 200
        assert (await http.post("/agent/v1/enroll", json={"token": offer.token})).status_code == 401
    private_key = await asyncio.to_thread(key.read_text)
    with pytest.raises(ValueError, match="only PEM certificates"):
        create_app(tmp_path / "invalid-gateway", client_ca_pem=private_key)
