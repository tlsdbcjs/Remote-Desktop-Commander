import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from racp_domain.models import RACPError
from racp_gateway.update_manifest import UpdateTrust, verify_release_manifest


def signed_manifest(
    tmp_path: Path,
    *,
    version: str = "0.1.17",
    platform: str = "win-x64",
    url: str = "https://updates.example/releases/gateway.zip",
    size: int = 1234,
    expires: datetime | None = None,
    protocol_major: int = 1,
    schema_min: int = 1,
    schema_max: int = 2,
    min_updater_version: str = "0.1.0",
    key_id: str | None = None,
) -> tuple[bytes, UpdateTrust]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    key = tmp_path / "update-public.pem"
    key.write_bytes(
        public.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_der = public.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    payload = {
        "schema_version": 1,
        "protocol_major": protocol_major,
        "release_id": "rel_017",
        "version": version,
        "platform": platform,
        "schema_min": schema_min,
        "schema_max": schema_max,
        "min_updater_version": min_updater_version,
        "package_url": url,
        "sha256": "a" * 64,
        "size_bytes": size,
        "key_id": key_id or hashlib.sha256(public_der).hexdigest(),
        "expires_at": (expires or datetime.now(UTC) + timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z"),
        "schema_rollback_compatible": True,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    raw = json.dumps(
        {
            "payload": payload,
            "signature": base64.b64encode(private.sign(canonical)).decode(),
        }
    ).encode()
    trust = UpdateTrust(
        public_key_file=key,
        allowed_origins=["https://updates.example"],
        current_version="0.1.16",
        updater_version="0.1.16",
        gateway_schema=2,
        platform="win-x64",
        max_package_bytes=4096,
    )
    return raw, trust


def test_signed_release_manifest_accepts_only_new_trusted_windows_release(tmp_path: Path) -> None:
    raw, trust = signed_manifest(tmp_path)
    release = verify_release_manifest(raw, trust)
    assert release.version == "0.1.17"
    assert release.platform == "win-x64"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "0.1.16"),
        ("platform", "linux-x64"),
        ("url", "https://evil.example/gateway.zip"),
        ("size", 5000),
        ("expires", datetime.now(UTC) - timedelta(seconds=1)),
        ("protocol_major", 2),
        ("schema_min", 3),
        ("schema_max", 0),
        ("min_updater_version", "9.0.0"),
        ("key_id", "0" * 64),
    ],
)
def test_manifest_rejects_incompatible_or_untrusted_release_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    raw, trust = signed_manifest(tmp_path, **{field: value})
    with pytest.raises(RACPError):
        verify_release_manifest(raw, trust)


def test_manifest_rejects_signature_tampering(tmp_path: Path) -> None:
    raw, trust = signed_manifest(tmp_path)
    document = json.loads(raw)
    document["payload"]["version"] = "0.1.18"
    with pytest.raises(RACPError):
        verify_release_manifest(json.dumps(document).encode(), trust)


def test_manifest_rejects_duplicate_keys_and_oversized_input(tmp_path: Path) -> None:
    raw, trust = signed_manifest(tmp_path)
    duplicate = raw.replace(b'"signature":', b'"payload":{},"signature":', 1)
    with pytest.raises(RACPError):
        verify_release_manifest(duplicate, trust)
    with pytest.raises(RACPError, match="64 KiB"):
        verify_release_manifest(raw + b" " * (64 * 1024), trust)
