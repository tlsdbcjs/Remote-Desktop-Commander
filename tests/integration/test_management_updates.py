import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from racp_domain.version import VERSION
from racp_gateway.app import create_app
from racp_gateway.config import GatewayConfig, GatewayUpdateConfig, write_gateway_config
from racp_gateway.console_auth import COOKIE
from racp_gateway.store import GatewayStore
from racp_sdk.security import digest

# This fixture represents an upgrade from the running source, not a fixed
# candidate that becomes equal-version whenever the workspace PATCH advances.
TARGET_VERSION = VERSION.rsplit(".", 1)[0] + "." + str(int(VERSION.rsplit(".", 1)[1]) + 1)


def _manifest(tmp_path: Path) -> tuple[bytes, Path]:
    private = Ed25519PrivateKey.generate()
    key = tmp_path / "update-public.pem"
    key.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_der = private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    payload = {
        "schema_version": 1,
        "protocol_major": 1,
        "release_id": "rel_020",
        "version": TARGET_VERSION,
        "platform": "win-x64",
        "schema_min": 1,
        "schema_max": 2,
        "min_updater_version": "0.1.0",
        "package_url": f"https://updates.example/gateway-{TARGET_VERSION}.zip",
        "sha256": "a" * 64,
        "size_bytes": 2048,
        "key_id": hashlib.sha256(public_der).hexdigest(),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "schema_rollback_compatible": True,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    raw = json.dumps(
        {
            "payload": payload,
            "signature": base64.b64encode(private.sign(canonical)).decode(),
        }
    ).encode()
    return raw, key


@pytest.mark.asyncio
async def test_web_update_check_and_portable_apply_are_signed_idempotent_and_deferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, trust_key = _manifest(tmp_path)
    root = tmp_path / "state"
    config_path = root / "config" / "gateway.json"
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_update_test",
            mode="portable",
            state_root=str(root.resolve()),
            public_origin="http://127.0.0.1:8765",
            update=GatewayUpdateConfig(
                feed_url="https://updates.example/stable/gateway.json",
                trust_key_file=str(trust_key.resolve()),
            ),
        ),
    )
    seeded = GatewayStore(root / "gateway.db")
    seeded.initialize(digest("owner-update-test"))
    seeded.close()
    monkeypatch.setattr(
        "racp_gateway.management.updates.fetch_signed_manifest",
        lambda url: raw,
    )
    app = create_app(root, gateway_config_path=config_path)
    credential, session = app.state.console_auth.create_session("owner_local")
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
        cookies={COOKIE: credential},
        headers={
            "X-CSRF-Token": session.csrf_token,
            "Origin": "http://127.0.0.1:8765",
        },
    )
    try:
        async with client:
            checked = await client.post(
                "/api/v1/management/updates/check",
                json={"expected_revision": 1},
            )
            assert checked.status_code == 200
            assert checked.json()["release_id"] == "rel_020"
            assert checked.json()["version"] == TARGET_VERSION
            assert "package_url" not in checked.json()

            payload = {
                "release_id": "rel_020",
                "expected_revision": 1,
                "idempotency_key": "web-update-020",
                "confirm": True,
            }
            applied = await client.post("/api/v1/management/updates/apply", json=payload)
            assert applied.status_code == 200
            assert applied.json()["state"] == "DEFERRED"
            duplicate = await client.post("/api/v1/management/updates/apply", json=payload)
            assert duplicate.status_code == 200
            assert duplicate.json()["id"] == applied.json()["id"]
            assert duplicate.json()["state"] == "DEFERRED"
            detail = await client.get(f"/api/v1/management/maintenance-jobs/{applied.json()['id']}")
            assert detail.status_code == 200
            assert detail.json()["state"] == "DEFERRED"

        candidate = root / "updates" / "candidates" / "rel_020.json"
        assert candidate.read_bytes() == raw
        backup_count = app.state.store.db.execute("SELECT COUNT(*) FROM backup_sets").fetchone()[0]
        assert int(backup_count) == 1
    finally:
        app.state.store.close()
