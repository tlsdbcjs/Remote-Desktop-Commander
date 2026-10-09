"""Signed release manifest verification for Gateway updates."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version
from pydantic import Field
from racp_domain.models import RACPError
from racp_protocol.models import Identifier, StrictModel


class UpdateTrust(StrictModel):
    public_key_file: Path
    allowed_origins: list[str] = Field(min_length=1, max_length=16)
    current_version: str = Field(max_length=64)
    updater_version: str = Field(max_length=64)
    gateway_schema: int = Field(ge=0)
    platform: Literal["win-x64"] = "win-x64"
    max_package_bytes: int = Field(default=2 * 1024**3, ge=1024, le=4 * 1024**3)


class ReleasePayload(StrictModel):
    schema_version: Literal[1] = 1
    protocol_major: Literal[1]
    release_id: Identifier
    version: str = Field(max_length=64)
    platform: Literal["win-x64"]
    schema_min: int = Field(ge=0)
    schema_max: int = Field(ge=0)
    min_updater_version: str = Field(max_length=64)
    package_url: str = Field(max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    key_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: str = Field(max_length=64)
    schema_rollback_compatible: bool = True


class SignedReleaseManifest(StrictModel):
    payload: ReleasePayload
    signature: str = Field(min_length=40, max_length=256)


class VerifiedRelease(ReleasePayload):
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _origin(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("update package URL must be an absolute HTTPS URL")
    if parts.query or parts.fragment:
        raise ValueError("update package URL must not contain query or fragment data")
    port = f":{parts.port}" if parts.port is not None else ""
    return f"https://{parts.hostname.lower()}{port}"


def _expires(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid release expiry") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("release expiry must use UTC")
    return parsed.astimezone(UTC)


def verify_release_manifest(raw: bytes, trust: UpdateTrust) -> VerifiedRelease:
    if len(raw) > 64 * 1024:
        raise RACPError("INVALID_ARGUMENT", "release manifest exceeds 64 KiB")
    try:
        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("release manifest contains duplicate JSON keys")
                result[key] = value
            return result

        document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        manifest = SignedReleaseManifest.model_validate(document)
        canonical = json.dumps(
            manifest.payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = base64.b64decode(manifest.signature, validate=True)
        public_key = serialization.load_pem_public_key(trust.public_key_file.read_bytes())
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("update trust key must be Ed25519")
        public_der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if manifest.payload.key_id != hashlib.sha256(public_der).hexdigest():
            raise ValueError("release manifest key id does not match the configured trust key")
        public_key.verify(signature, canonical)
        package_origin = _origin(manifest.payload.package_url)
        allowed = {_origin(value) for value in trust.allowed_origins}
        if package_origin not in allowed:
            raise ValueError("update package origin is not trusted")
        if manifest.payload.platform != trust.platform:
            raise ValueError("update package platform does not match this Gateway")
        if manifest.payload.size_bytes > trust.max_package_bytes:
            raise ValueError("update package exceeds configured size limit")
        if manifest.payload.schema_min > manifest.payload.schema_max:
            raise ValueError("release schema compatibility range is invalid")
        if not manifest.payload.schema_min <= trust.gateway_schema <= manifest.payload.schema_max:
            raise ValueError("release schema range does not support the active Gateway database")
        if _expires(manifest.payload.expires_at) <= datetime.now(UTC):
            raise ValueError("release manifest is expired")
        if Version(manifest.payload.version) <= Version(trust.current_version):
            raise ValueError("release is not newer than the active version")
        if Version(trust.updater_version) < Version(manifest.payload.min_updater_version):
            raise ValueError("release requires a newer Gateway updater")
    except (
        OSError,
        ValueError,
        InvalidVersion,
        InvalidSignature,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise RACPError("INVALID_ARGUMENT", "release manifest verification failed") from exc
    return VerifiedRelease(
        **manifest.payload.model_dump(),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )
