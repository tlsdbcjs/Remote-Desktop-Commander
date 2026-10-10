"""Signed update check and independent-updater handoff for the management Console."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from racp_domain.models import RACPError
from racp_domain.version import VERSION
from racp_protocol.management import (
    MaintenanceJobView,
    ManagementPrincipal,
    UpdateApplyRequest,
    UpdateCandidateView,
    UpdateCheckRequest,
    UpdateStatusView,
)
from racp_protocol.models import timestamp

from racp_gateway.backup import BackupManager
from racp_gateway.config import GatewayConfig, load_gateway_config
from racp_gateway.maintenance import MaintenanceCoordinator
from racp_gateway.management.authorization import authorize_management
from racp_gateway.migrations import CURRENT_GATEWAY_SCHEMA
from racp_gateway.update_manifest import UpdateTrust, VerifiedRelease, verify_release_manifest
from racp_gateway.updater_service import UpdaterIpcClient, UpdaterRequest, write_pending_request

ManifestFetch = Callable[[str], bytes]


def fetch_signed_manifest(url: str) -> bytes:
    chunks: list[bytes] = []
    received = 0
    with httpx.Client(follow_redirects=False, trust_env=False, timeout=15) as client:
        with client.stream("GET", url) as response:
            if 300 <= response.status_code < 400:
                raise RACPError("INVALID_ARGUMENT", "update feed redirect is not allowed")
            if response.status_code != 200:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE",
                    "update feed request failed",
                    status_code=response.status_code,
                )
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) > 64 * 1024:
                raise RACPError("RESOURCE_EXHAUSTED", "update manifest exceeds 64 KiB")
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > 64 * 1024:
                    raise RACPError("RESOURCE_EXHAUSTED", "update manifest exceeds 64 KiB")
                chunks.append(chunk)
    return b"".join(chunks)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


class UpdateManagement:
    def __init__(
        self,
        config_path: Path | None,
        *,
        data_dir: Path | None = None,
        maintenance: MaintenanceCoordinator | None = None,
        backups: BackupManager | None = None,
        fetch_manifest: ManifestFetch | None = None,
        ipc_client: UpdaterIpcClient | None = None,
    ) -> None:
        self.config_path = config_path
        self.data_dir = data_dir
        self.maintenance = maintenance
        self.backups = backups
        self.fetch_manifest = fetch_manifest or fetch_signed_manifest
        self.ipc_client = ipc_client or UpdaterIpcClient()

    def status(self, principal: ManagementPrincipal) -> UpdateStatusView:
        authorize_management(principal, "updates.read")
        if self.config_path is None:
            return UpdateStatusView(
                configured=False,
                current_version=VERSION,
                channel="stable",
                feed_configured=False,
                trust_key_configured=False,
                automatic_check=False,
                apply_enabled=False,
                reason="versioned Gateway configuration is not active",
            )
        try:
            config = load_gateway_config(self.config_path)
        except (OSError, ValueError):
            return UpdateStatusView(
                configured=False,
                current_version=VERSION,
                channel="stable",
                feed_configured=False,
                trust_key_configured=False,
                automatic_check=False,
                apply_enabled=False,
                reason="Gateway update configuration could not be loaded",
            )
        feed = bool(config.update.feed_url)
        trust = bool(config.update.trust_key_file)
        enabled = feed and trust
        return UpdateStatusView(
            configured=True,
            current_version=VERSION,
            channel=config.update.channel,
            feed_configured=feed,
            trust_key_configured=trust,
            automatic_check=config.update.automatic_check,
            apply_enabled=enabled,
            reason=None if enabled else "signed feed and trust public key are both required",
        )

    def _configured(self, expected_revision: int) -> GatewayConfig:
        if self.config_path is None or self.data_dir is None:
            raise RACPError("CAPABILITY_UNAVAILABLE", "versioned Gateway updates are not active")
        try:
            config = load_gateway_config(self.config_path)
        except (OSError, ValueError) as exc:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "Gateway update configuration is invalid",
            ) from exc
        if config.revision != expected_revision:
            raise RACPError("CONFLICT", "Gateway configuration revision changed")
        if not config.update.feed_url or not config.update.trust_key_file:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "signed update feed and trust public key are required",
            )
        return config

    def _verify(self, raw: bytes, expected_revision: int) -> VerifiedRelease:
        config = self._configured(expected_revision)
        assert config.update.feed_url is not None
        assert config.update.trust_key_file is not None
        trust = UpdateTrust(
            public_key_file=Path(config.update.trust_key_file),
            allowed_origins=[_origin(config.update.feed_url)],
            current_version=VERSION,
            updater_version=VERSION,
            gateway_schema=CURRENT_GATEWAY_SCHEMA,
            platform="win-x64",
        )
        return verify_release_manifest(raw, trust)

    def _candidate_path(self, release_id: str) -> Path:
        assert self.data_dir is not None
        return self.data_dir / "updates" / "candidates" / f"{release_id}.json"

    def check(
        self,
        principal: ManagementPrincipal,
        request: UpdateCheckRequest,
    ) -> UpdateCandidateView:
        authorize_management(principal, "updates.write")
        config = self._configured(request.expected_revision)
        assert config.update.feed_url is not None
        raw = self.fetch_manifest(config.update.feed_url)
        release = self._verify(raw, request.expected_revision)
        candidate = self._candidate_path(release.release_id)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        temporary = candidate.with_suffix(".tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, candidate)
        return UpdateCandidateView(
            release_id=release.release_id,
            version=release.version,
            platform=release.platform,
            manifest_sha256=release.manifest_sha256,
            schema_rollback_compatible=release.schema_rollback_compatible,
            checked_at=timestamp(),
        )

    async def apply(
        self,
        principal: ManagementPrincipal,
        request: UpdateApplyRequest,
    ) -> MaintenanceJobView:
        authorize_management(principal, "updates.write")
        if self.maintenance is None or self.backups is None:
            raise RACPError("CAPABILITY_UNAVAILABLE", "update maintenance services are not active")
        config = self._configured(request.expected_revision)
        candidate = self._candidate_path(request.release_id)
        candidate_invalid = (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size > 64 * 1024
        )
        if candidate_invalid:
            raise RACPError("NOT_FOUND", "verified update candidate was not found")
        raw = candidate.read_bytes()
        release = self._verify(raw, request.expected_revision)
        if release.release_id != request.release_id:
            raise RACPError("CONFLICT", "update candidate identity changed")
        job = self.maintenance.begin(principal, "update", request.idempotency_key)
        if job.state != "PENDING":
            return job
        report = await self.maintenance.drain()
        if report.state != "READY":
            self.maintenance.end()
            return self.maintenance.set_state(
                job.id,
                "DEFERRED",
                error="maintenance drain timed out",
            )
        self.maintenance.set_state(job.id, "RUNNING", progress=0.05)
        try:
            backup = self.backups.create(job.id + "-pre-update")
            if config.mode != "service":
                self.maintenance.end()
                return self.maintenance.set_state(
                    job.id,
                    "DEFERRED",
                    progress=0.1,
                    receipt={
                        "release_id": release.release_id,
                        "pre_update_backup_id": backup.id,
                        "reason": "portable mode requires explicit host replacement",
                    },
                )
            assert self.data_dir is not None
            pending = self.data_dir / "updates" / "pending.json"
            updater_request = UpdaterRequest.create(
                instance_id=config.instance_id,
                release_id=release.release_id,
                job_id=job.id,
                expected_revision=config.revision,
                pre_update_backup_id=backup.id,
            )
            write_pending_request(pending, updater_request)
            self.ipc_client.submit(updater_request)
            return self.maintenance.get(principal, job.id)
        except BaseException as exc:
            self.maintenance.end()
            self.maintenance.set_state(job.id, "FAILED", error=str(exc)[:2048])
            raise
