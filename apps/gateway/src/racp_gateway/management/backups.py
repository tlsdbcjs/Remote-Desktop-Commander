"""Management facade for maintenance-scoped backup and restore validation."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from racp_domain.models import RACPError
from racp_protocol.management import (
    BackupSetView,
    MaintenanceJobView,
    ManagementPrincipal,
    RestoreCreateRequest,
    RestorePreview,
)

from racp_gateway.backup import BackupManager
from racp_gateway.config import load_gateway_config, resolve_gateway_paths
from racp_gateway.maintenance import MaintenanceCoordinator
from racp_gateway.management.authorization import authorize_management
from racp_gateway.restore import RestoreManager


class BackupManagement:
    def __init__(
        self,
        coordinator: MaintenanceCoordinator,
        backups: BackupManager,
        restore: RestoreManager,
        restore_root: Path,
    ) -> None:
        self.coordinator = coordinator
        self.backups = backups
        self.restore = restore
        self.restore_root = restore_root

    async def create(
        self,
        principal: ManagementPrincipal,
        key: str,
    ) -> BackupSetView:
        authorize_management(principal, "backups.write")
        job = self.coordinator.begin(principal, "backup", key)
        if job.state == "SUCCEEDED" and job.receipt_id:
            row = self.backups.store.db.execute(
                "SELECT payload FROM maintenance_receipts WHERE id=?",
                (job.receipt_id,),
            ).fetchone()
            if row is not None:
                payload = json.loads(row["payload"])
                backup = self.backups.store.db.execute(
                    "SELECT * FROM backup_sets WHERE id=?",
                    (payload["backup_id"],),
                ).fetchone()
                if backup is not None:
                    return BackupSetView(**dict(backup))
        report = await self.coordinator.drain(timeout_seconds=0)
        if report.state != "READY":
            self.coordinator.set_state(job.id, "DEFERRED")
            self.coordinator.end()
            raise RACPError(
                "CONFLICT",
                "maintenance drain is deferred while active work remains",
                active_operations=report.active_operations,
                active_handles=report.active_handles,
            )
        self.coordinator.set_state(job.id, "RUNNING", progress=0.1)
        try:
            result = self.backups.create(job.id)
            self.coordinator.set_state(
                job.id,
                "SUCCEEDED",
                progress=1.0,
                receipt={"backup_id": result.id},
            )
            return result
        except BaseException as exc:
            self.coordinator.set_state(job.id, "FAILED", error=str(exc)[:2048])
            raise
        finally:
            self.coordinator.end()

    def validate_restore(
        self,
        principal: ManagementPrincipal,
        backup_id: str,
    ) -> RestorePreview:
        authorize_management(principal, "backups.write")
        return self.restore.validate(backup_id)

    @staticmethod
    def _seed_restore_job(database: Path, row: sqlite3.Row) -> None:
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO maintenance_jobs("
                "id,kind,state,actor_id,realm_id,idempotency_key,created_at,updated_at,"
                "progress,error,receipt_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                tuple(row[key] for key in row.keys()),
            )
            connection.commit()
        finally:
            connection.close()

    def _restore_handoff(
        self,
        job: MaintenanceJobView,
        staged: dict[str, str],
        pre_restore: BackupSetView,
    ) -> Path:
        if self.backups.config_path is None:
            raise RACPError("CAPABILITY_UNAVAILABLE", "restore requires versioned Gateway config")
        config = load_gateway_config(self.backups.config_path)
        paths = resolve_gateway_paths(config)
        pre_root = Path(pre_restore.manifest_path).parent.resolve()
        stage_root = Path(staged["gateway.db"]).parent.resolve()
        replacements: list[dict[str, str | None]] = [
            {
                "source": staged["gateway.db"],
                "target": str(paths.database),
                "rollback_source": str(pre_root / "gateway.db"),
            }
        ]
        if "gateway.json" in staged:
            replacements.append(
                {
                    "source": staged["gateway.json"],
                    "target": str(paths.config_file),
                    "rollback_source": str(pre_root / "gateway.json"),
                }
            )
        for prefix, target_root in (("secrets/", paths.secrets), ("artifacts/", paths.artifacts)):
            for relative, source in sorted(staged.items()):
                if not relative.startswith(prefix):
                    continue
                suffix = Path(relative[len(prefix) :])
                rollback = pre_root / relative
                replacements.append(
                    {
                        "source": source,
                        "target": str(target_root / suffix),
                        "rollback_source": str(rollback) if rollback.is_file() else None,
                    }
                )
        receipt = self.restore_root / "receipts" / f"{job.id}.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        handoff = stage_root / "handoff.json"
        payload = {
            "schema_version": 1,
            "job_id": job.id,
            "state_root": str(paths.state_root),
            "service_name": "RACP Gateway",
            "health_url": f"{'https' if config.tls.certificate_file else 'http'}://"
            f"127.0.0.1:{config.port}/healthz",
            "receipt_path": str(receipt),
            "replacements": replacements,
        }
        temporary = handoff.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, handoff)
        return handoff

    @staticmethod
    def _launch_restore_helper(handoff: Path) -> None:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "racp_gateway.restore_service",
                "--handoff",
                str(handoff),
                "--delay-seconds",
                "1",
            ],
            close_fds=True,
            creationflags=flags,
        )

    async def request_restore(
        self,
        principal: ManagementPrincipal,
        request: RestoreCreateRequest,
    ) -> MaintenanceJobView:
        if principal.role != "owner":
            raise RACPError("PERMISSION_DENIED", "restore requires owner role")
        if self.backups.config_path is None:
            raise RACPError("CAPABILITY_UNAVAILABLE", "restore requires versioned Gateway config")
        config = load_gateway_config(self.backups.config_path)
        if config.instance_id != request.expected_instance_id:
            raise RACPError("CONFLICT", "restore instance identity changed")
        if config.revision != request.expected_revision:
            raise RACPError("CONFLICT", "Gateway configuration revision changed")
        preview = self.restore.validate(request.backup_id)
        if not preview.valid or preview.instance_id != config.instance_id:
            raise RACPError("INVALID_ARGUMENT", "backup is not valid for this Gateway instance")
        job = self.coordinator.begin(principal, "restore", request.idempotency_key)
        if job.state in {"RUNNING", "DEFERRED", "SUCCEEDED"}:
            return job
        report = await self.coordinator.drain()
        if report.state != "READY":
            self.coordinator.end()
            return self.coordinator.set_state(
                job.id,
                "DEFERRED",
                error="maintenance drain timed out",
            )
        self.coordinator.set_state(job.id, "RUNNING", progress=0.1)
        try:
            pre_restore = self.backups.create(job.id + "-pre-restore")
            stage_root = self.restore_root / job.id
            staged = self.restore.stage(request.backup_id, stage_root)
            row = self.backups.store.db.execute(
                "SELECT * FROM maintenance_jobs WHERE id=?",
                (job.id,),
            ).fetchone()
            assert row is not None
            self._seed_restore_job(Path(staged["gateway.db"]), row)
            handoff = self._restore_handoff(job, staged, pre_restore)
            if config.mode != "service":
                self.coordinator.end()
                return self.coordinator.set_state(
                    job.id,
                    "DEFERRED",
                    progress=0.9,
                    receipt={
                        "backup_id": request.backup_id,
                        "pre_restore_backup_id": pre_restore.id,
                        "handoff": str(handoff),
                        "reason": "portable mode requires host restart apply",
                    },
                )
            self._launch_restore_helper(handoff)
            return self.coordinator.get(principal, job.id)
        except BaseException as exc:
            self.coordinator.end()
            self.coordinator.set_state(job.id, "FAILED", error=str(exc)[:2048])
            raise
