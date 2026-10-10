"""Versioned Gateway configuration and local path validation."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography import x509
from pydantic import Field
from racp_protocol.management import ConfigCheck, GatewayPaths
from racp_protocol.models import Identifier, StrictModel

from racp_gateway.network import checked_origin


class GatewayTlsConfig(StrictModel):
    certificate_file: str | None = Field(default=None, max_length=4096)
    private_key_file: str | None = Field(default=None, max_length=4096)
    client_ca_file: str | None = Field(default=None, max_length=4096)


class GatewayAuthConfig(StrictModel):
    oauth_config_file: str | None = Field(default=None, max_length=4096)
    local_mcp_port: int | None = Field(default=None, ge=1, le=65535)


class GatewayRetentionConfig(StrictModel):
    diagnostics_days: int = Field(default=30, ge=1, le=3650)
    diagnostics_max_bytes: int = Field(default=1024**3, ge=1024**2, le=1024**4)
    audit_days: int = Field(default=180, ge=1, le=3650)
    backup_sets: int = Field(default=14, ge=1, le=365)


class GatewayUpdateConfig(StrictModel):
    channel: str = Field(default="stable", pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    feed_url: str | None = Field(default=None, max_length=2048)
    trust_key_file: str | None = Field(default=None, max_length=4096)
    automatic_check: bool = True


class GatewayConfig(StrictModel):
    schema_version: Literal[1] = 1
    instance_id: Identifier
    mode: Literal["service", "portable"] = "portable"
    bind_address: str = Field(default="127.0.0.1", max_length=255)
    port: int = Field(default=8765, ge=1, le=65535)
    public_origin: str | None = Field(default=None, max_length=2048)
    state_root: str = Field(max_length=4096)
    tls: GatewayTlsConfig = Field(default_factory=GatewayTlsConfig)
    auth: GatewayAuthConfig = Field(default_factory=GatewayAuthConfig)
    retention: GatewayRetentionConfig = Field(default_factory=GatewayRetentionConfig)
    update: GatewayUpdateConfig = Field(default_factory=GatewayUpdateConfig)
    revision: int = Field(default=1, ge=1)


def _local_absolute(value: str, *, base: Path | None = None) -> Path:
    raw = Path(value).expanduser()
    path = raw if raw.is_absolute() else (base or Path.cwd()) / raw
    return path.resolve(strict=False)


def resolve_gateway_paths(config: GatewayConfig) -> GatewayPaths:
    state_root = _local_absolute(config.state_root)
    release_root = Path(os.environ.get("RACP_GATEWAY_RELEASE_ROOT", sys.executable)).resolve()
    if release_root.is_file():
        release_root = release_root.parent
    return GatewayPaths(
        release_root=release_root,
        config_file=state_root / "config" / "gateway.json",
        state_root=state_root,
        database=state_root / "gateway.db",
        secrets=state_root / "secrets",
        logs=state_root / "logs",
        artifacts=state_root / "artifacts",
        backups=state_root / "backups",
        update_staging=state_root / "updates" / "staging",
    )


def _is_unc(path: Path) -> bool:
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


def _path_error(path: Path, label: str) -> str | None:
    if not path.is_absolute():
        return f"{label} must be an absolute local path"
    if _is_unc(path):
        return f"{label} must not use a UNC path"
    for candidate in [path, *path.parents]:
        if candidate.exists() and candidate.is_symlink():
            return f"{label} must not traverse a symlink"
    return None


def validate_gateway_config(config: GatewayConfig, *, check_listener: bool = False) -> ConfigCheck:
    errors: list[str] = []
    warnings: list[str] = []
    state_root = _local_absolute(config.state_root)
    issue = _path_error(state_root, "state_root")
    if issue:
        errors.append(issue)
    cert = config.tls.certificate_file
    key = config.tls.private_key_file
    if (cert is None) != (key is None):
        errors.append("TLS certificate and private key must be configured together")
    has_tls = cert is not None and key is not None
    try:
        address = ipaddress.ip_address(config.bind_address)
        loopback = address.is_loopback
    except ValueError:
        loopback = config.bind_address == "localhost"
        if not loopback:
            errors.append("bind_address must be a literal local IP address or localhost")
    if not loopback and (not has_tls or config.public_origin is None):
        errors.append("non-loopback listener requires TLS and an explicit public origin")
    if config.public_origin is not None:
        try:
            origin = checked_origin(config.public_origin)
            if urlsplit(origin).scheme != ("https" if has_tls else "http"):
                errors.append("public origin scheme must match the configured listener transport")
        except ValueError as exc:
            errors.append(str(exc))
    if config.auth.local_mcp_port == config.port:
        errors.append("local MCP port must differ from the Gateway listener port")
    if config.auth.local_mcp_port is not None and (
        not has_tls or config.public_origin is None or config.auth.oauth_config_file is None
    ):
        errors.append("local MCP requires TLS, public origin and OAuth configuration")
    for raw, label in (
        (cert, "TLS certificate"),
        (key, "TLS private key"),
        (config.tls.client_ca_file, "client CA"),
        (config.auth.oauth_config_file, "OAuth configuration"),
        (config.update.trust_key_file, "update trust key"),
    ):
        if raw:
            candidate = _local_absolute(raw)
            path_issue = _path_error(candidate, label)
            if path_issue:
                errors.append(path_issue)
            elif not candidate.is_file():
                errors.append(f"{label} does not exist")
    if has_tls and cert and key and not errors:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(_local_absolute(cert), _local_absolute(key))
            certificate = x509.load_pem_x509_certificate(_local_absolute(cert).read_bytes())
            now = datetime.now(UTC)
            if certificate.not_valid_after_utc <= now:
                errors.append("TLS certificate is expired")
            elif (certificate.not_valid_after_utc - now).total_seconds() < 30 * 86400:
                warnings.append("TLS certificate expires within 30 days")
            if config.public_origin:
                host = urlsplit(config.public_origin).hostname or ""
                try:
                    san = certificate.extensions.get_extension_for_class(
                        x509.SubjectAlternativeName
                    ).value
                    try:
                        address = ipaddress.ip_address(host)
                    except ValueError:
                        names = {value.lower() for value in san.get_values_for_type(x509.DNSName)}
                        if host.lower() not in names:
                            errors.append("TLS certificate SAN does not include public origin host")
                    else:
                        if address not in san.get_values_for_type(x509.IPAddress):
                            errors.append("TLS certificate SAN does not include public origin IP")
                except x509.ExtensionNotFound:
                    errors.append("TLS certificate has no subject alternative name")
        except (OSError, ValueError, ssl.SSLError) as exc:
            errors.append(f"TLS certificate/key validation failed: {exc}")
    if config.update.feed_url is not None:
        try:
            feed = urlsplit(config.update.feed_url)
            if (
                feed.scheme != "https"
                or not feed.hostname
                or feed.username
                or feed.password
                or feed.query
                or feed.fragment
            ):
                raise ValueError(
                    "update feed must be an absolute HTTPS URL without credentials, "
                    "query or fragment"
                )
            if any(ord(char) < 32 or ord(char) == 127 for char in config.update.feed_url):
                raise ValueError("update feed contains control characters")
        except (ValueError, UnicodeError) as exc:
            errors.append(f"invalid update feed: {exc}")
    if check_listener and not errors:
        family = socket.AF_INET6 if ":" in config.bind_address else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((config.bind_address, config.port))
        except OSError as exc:
            errors.append(f"Gateway listener could not bind: {exc}")
    return ConfigCheck(valid=not errors, errors=errors, warnings=warnings, restart_required=False)


def load_gateway_config(path: Path) -> GatewayConfig:
    source = path.expanduser().resolve(strict=True)
    if source.stat().st_size > 64 * 1024:
        raise ValueError("Gateway configuration exceeds 64 KiB")
    config = GatewayConfig.model_validate_json(source.read_bytes())
    state = Path(config.state_root).expanduser()
    if not state.is_absolute():
        config = config.model_copy(update={"state_root": str((source.parent / state).resolve())})
    check = validate_gateway_config(config)
    if not check.valid:
        raise ValueError("; ".join(check.errors))
    return config


def write_gateway_config(
    path: Path, config: GatewayConfig, *, expected_revision: int | None = None
) -> None:
    destination = path.expanduser().resolve(strict=False)
    if destination.exists() and expected_revision is not None:
        current = load_gateway_config(destination)
        if current.revision != expected_revision:
            raise ValueError("Gateway configuration revision conflict")
    check = validate_gateway_config(config)
    if not check.valid:
        raise ValueError("; ".join(check.errors))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
