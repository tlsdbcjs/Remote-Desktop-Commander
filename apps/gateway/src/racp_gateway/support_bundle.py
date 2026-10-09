"""Bounded support bundle generation without raw database or credential material."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field
from racp_domain.version import VERSION
from racp_observability.logging import sanitize_fields
from racp_protocol.models import Identifier, StrictModel, new_id, timestamp

from racp_gateway.config import load_gateway_config
from racp_gateway.store import GatewayStore


@dataclass(frozen=True)
class SupportBundleRequest:
    store: GatewayStore
    output_dir: Path
    config_path: Path | None = None
    log_limit: int = 200
    max_bytes: int = 5 * 1024 * 1024


class SupportBundleReceipt(StrictModel):
    id: Identifier
    file_name: str = Field(max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    created_at: str
    log_entries: int = Field(ge=0)


def _masked_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"configured": False}
    config = load_gateway_config(path)
    return {
        "configured": True,
        "schema_version": config.schema_version,
        "instance_id": config.instance_id,
        "mode": config.mode,
        "bind_address": config.bind_address,
        "port": config.port,
        "public_origin": config.public_origin,
        "revision": config.revision,
        "tls": {
            "configured": bool(config.tls.certificate_file and config.tls.private_key_file),
            "client_ca_configured": bool(config.tls.client_ca_file),
        },
        "auth": {
            "oauth_configured": bool(config.auth.oauth_config_file),
            "local_mcp_port": config.auth.local_mcp_port,
        },
        "retention": config.retention.model_dump(mode="json"),
        "update": {
            "channel": config.update.channel,
            "feed_configured": bool(config.update.feed_url),
            "trust_key_configured": bool(config.update.trust_key_file),
            "automatic_check": config.update.automatic_check,
        },
    }


def _support_logs(store: GatewayStore, limit: int) -> list[dict[str, Any]]:
    bounded = max(1, min(limit, 500))
    allowed_fields = {"code", "status", "count", "safe_code", "component"}
    rows = store.db.execute(
        "SELECT timestamp,level,event,device_id,request_id,actor_id,fields_json "
        "FROM diagnostic_logs ORDER BY timestamp DESC,id DESC LIMIT ?",
        (bounded,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            fields = json.loads(row["fields_json"])
        except (TypeError, json.JSONDecodeError):
            fields = {}
        fields = {
            key: value
            for key, value in sanitize_fields(fields).items()
            if key in allowed_fields
        }
        result.append(
            {
                "timestamp": row["timestamp"],
                "level": row["level"],
                "event": row["event"],
                "device_id": row["device_id"],
                "request_id": row["request_id"],
                "actor_id": row["actor_id"],
                "fields": fields,
            }
        )
    return result


def create_support_bundle(request: SupportBundleRequest) -> SupportBundleReceipt:
    if request.max_bytes < 4096 or request.max_bytes > 100 * 1024 * 1024:
        raise ValueError("support bundle size limit is invalid")
    request.output_dir.mkdir(parents=True, exist_ok=True)
    bundle_id = new_id("sup")
    file_name = f"racp-support-{bundle_id}.zip"
    output = request.output_dir / file_name
    if output.exists():
        raise FileExistsError(output)
    logs = _support_logs(request.store, request.log_limit)
    manifest = {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "version": VERSION,
        "created_at": timestamp(),
        "contents": ["manifest.json", "config.json", "logs.json"],
        "excludes": [
            "gateway.db",
            "private keys",
            "cookies",
            "authorization headers",
            "operation output",
            "raw command output",
        ],
    }
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{bundle_id}.",
        suffix=".zip",
        dir=request.output_dir,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )
            archive.writestr(
                "config.json",
                json.dumps(_masked_config(request.config_path), indent=2, sort_keys=True)
                + "\n",
            )
            archive.writestr(
                "logs.json",
                json.dumps(logs, indent=2, sort_keys=True) + "\n",
            )
        if temporary.stat().st_size > request.max_bytes:
            raise ValueError("support bundle exceeds configured size limit")
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    with output.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return SupportBundleReceipt(
        id=bundle_id,
        file_name=file_name,
        sha256=checksum,
        size_bytes=output.stat().st_size,
        created_at=str(manifest["created_at"]),
        log_entries=len(logs),
    )
