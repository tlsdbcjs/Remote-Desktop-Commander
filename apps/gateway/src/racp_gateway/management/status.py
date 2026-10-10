"""Gateway management status assembled from runtime and versioned configuration."""

from __future__ import annotations

import ipaddress
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography import x509
from racp_domain.version import VERSION
from racp_protocol.management import GatewayStatusView, ManagementPrincipal

from racp_gateway.config import load_gateway_config
from racp_gateway.management.authorization import authorize_management
from racp_gateway.management.store import ManagementStore
from racp_gateway.store import GatewayStore


class GatewayStatusService:
    def __init__(
        self,
        store: GatewayStore,
        management: ManagementStore,
        *,
        config_path: Path | None,
        oauth_configured: bool,
        event_stream_count: Callable[[], int],
    ) -> None:
        self.store = store
        self.management = management
        self.config_path = config_path
        self.oauth_configured = oauth_configured
        self.event_stream_count = event_stream_count

    def snapshot(self, principal: ManagementPrincipal) -> GatewayStatusView:
        authorize_management(principal, "status.read")
        warnings: list[str] = []
        database: Literal["ready", "busy", "failed"] = "ready"
        try:
            self.store.db.execute("SELECT 1").fetchone()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                database = "busy"
                warnings.append("database is busy")
            else:
                database = "failed"
                warnings.append("database check failed")
        except Exception:
            database = "failed"
            warnings.append("database check failed")

        try:
            installed_version = package_version("racp-domain")
        except PackageNotFoundError:
            installed_version = VERSION
        if installed_version != VERSION:
            warnings.append(
                f"source/package version mismatch: source={VERSION}, package={installed_version}"
            )

        config = None
        if self.config_path is not None:
            try:
                config = load_gateway_config(self.config_path)
            except (OSError, ValueError):
                warnings.append("versioned configuration could not be loaded")

        tls_expires_at = None
        tls_configured = bool(config and config.tls.certificate_file)
        if config and config.tls.certificate_file:
            try:
                certificate = x509.load_pem_x509_certificate(
                    Path(config.tls.certificate_file).read_bytes()
                )
                expires = certificate.not_valid_after_utc.astimezone(UTC)
                tls_expires_at = expires.isoformat().replace("+00:00", "Z")
                if (expires - datetime.now(UTC)).total_seconds() < 30 * 86400:
                    warnings.append("TLS certificate expires within 30 days")
            except (OSError, ValueError):
                warnings.append("TLS certificate could not be inspected")

        if config is not None:
            state_root = Path(config.state_root)
        else:
            database_row = self.store.db.execute("PRAGMA database_list").fetchone()
            database_path = (
                Path(str(database_row[2]))
                if database_row and database_row[2]
                else Path.cwd()
            )
            state_root = database_path.parent
        disk_free_bytes = None
        try:
            disk_free_bytes = shutil.disk_usage(state_root).free
            if disk_free_bytes < 512 * 1024 * 1024:
                warnings.append("state volume has less than 512 MiB free")
        except OSError:
            warnings.append("state volume free space could not be measured")

        total_devices = 0
        connected_devices = 0
        settings_revision = config.revision if config is not None else 1
        if database == "ready":
            try:
                total_devices = int(
                    self.store.db.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
                )
                stale_cutoff = (datetime.now(UTC) - timedelta(seconds=30)).isoformat().replace(
                    "+00:00", "Z"
                )
                connected_devices = int(
                    self.store.db.execute(
                        "SELECT COUNT(*) FROM devices "
                        "WHERE revoked=0 AND "
                        "COALESCE(json_extract(info,'$.status'),'OFFLINE') "
                        "IN ('ONLINE','DEGRADED') AND "
                        "COALESCE(json_extract(info,'$.last_seen_at'),'')>=?",
                        (stale_cutoff,),
                    ).fetchone()[0]
                )
                stale_devices = int(
                    self.store.db.execute(
                        "SELECT COUNT(*) FROM devices "
                        "WHERE revoked=0 AND "
                        "COALESCE(json_extract(info,'$.status'),'OFFLINE') "
                        "IN ('ONLINE','DEGRADED') AND "
                        "COALESCE(json_extract(info,'$.last_seen_at'),'')<?",
                        (stale_cutoff,),
                    ).fetchone()[0]
                )
                if stale_devices:
                    warnings.append(f"{stale_devices} device status records are stale")
                if config is None:
                    settings_revision = self.management.get_revision()
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    database = "busy"
                    warnings.append("database became busy while reading status")
                else:
                    database = "failed"
                    warnings.append("database status query failed")
        mcp: Literal["owner_bearer", "oauth", "not_configured"]
        if self.oauth_configured:
            mcp = "oauth"
        elif config is None or config.public_origin is None:
            mcp = "owner_bearer"
        else:
            host = urlsplit(config.public_origin).hostname or ""
            try:
                local_origin = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local_origin = host.lower() == "localhost"
            mcp = "owner_bearer" if local_origin else "not_configured"
            if mcp == "not_configured":
                warnings.append("remote MCP OAuth is not configured")

        ready = database == "ready"
        return GatewayStatusView(
            lifecycle="READY" if ready and not warnings else "DEGRADED",
            ready=ready,
            version=VERSION,
            instance_id=config.instance_id if config else None,
            mode=config.mode if config else "legacy",
            setup_mode="oidc" if self.oauth_configured else "local_owner",
            database=database,
            mcp=mcp,
            settings_revision=settings_revision,
            connected_devices=connected_devices,
            total_devices=total_devices,
            event_streams=max(0, int(self.event_stream_count())),
            tls_configured=tls_configured,
            tls_expires_at=tls_expires_at,
            disk_free_bytes=disk_free_bytes,
            warnings=warnings,
        )
