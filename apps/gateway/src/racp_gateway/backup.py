"""Consistent local Gateway backup sets using SQLite's online backup API."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from racp_domain.models import RACPError
from racp_protocol.management import BackupSetView
from racp_protocol.models import new_id, timestamp

from racp_gateway.config import load_gateway_config
from racp_gateway.migrations import CURRENT_GATEWAY_SCHEMA
from racp_gateway.store import GatewayStore


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class BackupManager:
    def __init__(
        self,
        store: GatewayStore,
        root: Path,
        *,
        instance_id: str,
        config_path: Path | None = None,
        artifacts_root: Path | None = None,
        secrets_root: Path | None = None,
    ) -> None:
        self.store = store
        self.root = root
        self.instance_id = instance_id
        self.config_path = config_path
        self.artifacts_root = artifacts_root
        self.secrets_root = secrets_root

    def _estimated_bytes(self) -> int:
        page_count = int(self.store.db.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(self.store.db.execute("PRAGMA page_size").fetchone()[0])
        total = page_count * page_size
        if self.config_path is not None and self.config_path.is_file():
            total += self.config_path.stat().st_size
        if self.secrets_root is not None and self.secrets_root.is_dir():
            total += sum(
                path.stat().st_size
                for path in self.secrets_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
        if self.artifacts_root is not None:
            has_artifacts = self.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'"
            ).fetchone()
            if has_artifacts:
                total += int(
                    self.store.db.execute(
                        "SELECT COALESCE(SUM(size_bytes),0) FROM artifacts WHERE state='READY'"
                    ).fetchone()[0]
                )
        return total + 16 * 1024 * 1024

    def _require_space(self) -> None:
        probe = self.root.resolve(strict=False)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        required = self._estimated_bytes()
        free = shutil.disk_usage(probe).free
        if free < required:
            raise RACPError(
                "RESOURCE_EXHAUSTED",
                "insufficient disk space for Gateway backup",
                required_bytes=required,
                free_bytes=free,
            )

    @staticmethod
    def _copy_manifest_file(
        source: Path,
        destination: Path,
        relative: str,
        files: list[dict[str, object]],
    ) -> None:
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append(
            {
                "path": relative.replace("\\", "/"),
                "sha256": _sha256(target),
                "bytes": target.stat().st_size,
            }
        )

    def create(self, job_id: str) -> BackupSetView:
        self._require_space()
        backup_id = new_id("bak")
        destination = self.root / backup_id
        staging = self.root / f".{backup_id}.tmp"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            database = staging / "gateway.db"
            target = sqlite3.connect(database)
            try:
                self.store.db.backup(target)
            finally:
                target.close()
            files: list[dict[str, object]] = [
                {
                    "path": "gateway.db",
                    "sha256": _sha256(database),
                    "bytes": database.stat().st_size,
                }
            ]
            if self.config_path is not None and self.config_path.is_file():
                self._copy_manifest_file(
                    self.config_path,
                    staging,
                    "gateway.json",
                    files,
                )
                config = load_gateway_config(self.config_path)
                tls_sources = {
                    "tls/certificate.pem": config.tls.certificate_file,
                    "tls/private-key.pem": config.tls.private_key_file,
                    "tls/client-ca.pem": config.tls.client_ca_file,
                }
                for relative, raw in tls_sources.items():
                    if raw:
                        self._copy_manifest_file(Path(raw), staging, relative, files)
            if self.secrets_root is not None and self.secrets_root.is_dir():
                for source in sorted(self.secrets_root.rglob("*")):
                    if not source.is_file():
                        continue
                    secret_relative = source.relative_to(self.secrets_root)
                    if source.is_symlink() or ".." in secret_relative.parts:
                        raise ValueError("secret backup path is unsafe")
                    self._copy_manifest_file(
                        source,
                        staging,
                        (Path("secrets") / secret_relative).as_posix(),
                        files,
                    )
            if self.artifacts_root is not None:
                snapshot = sqlite3.connect(database)
                snapshot.row_factory = sqlite3.Row
                try:
                    has_artifacts = snapshot.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'"
                    ).fetchone()
                    artifacts = (
                        snapshot.execute(
                            "SELECT DISTINCT sha256,size_bytes FROM artifacts "
                            "WHERE state='READY' ORDER BY sha256"
                        ).fetchall()
                        if has_artifacts
                        else []
                    )
                finally:
                    snapshot.close()
                for artifact in artifacts:
                    digest = str(artifact["sha256"])
                    source = self.artifacts_root / digest
                    if not source.is_file() or source.stat().st_size != int(artifact["size_bytes"]):
                        raise RuntimeError("artifact changed while backup was being captured")
                    self._copy_manifest_file(
                        source,
                        staging,
                        f"artifacts/{digest}",
                        files,
                    )
            manifest = {
                "backup_id": backup_id,
                "job_id": job_id,
                "instance_id": self.instance_id,
                "schema_version": CURRENT_GATEWAY_SCHEMA,
                "created_at": timestamp(),
                "files": files,
            }
            manifest_path = staging / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_hash = _sha256(manifest_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, destination)
            final_manifest = destination / "manifest.json"
            with self.store.transaction():
                self.store.db.execute(
                    "INSERT INTO backup_sets("
                    "id,state,manifest_path,sha256,schema_version,instance_id,created_at"
                    ") VALUES (?,?,?,?,?,?,?)",
                    (
                        backup_id,
                        "COMPLETE",
                        str(final_manifest),
                        manifest_hash,
                        CURRENT_GATEWAY_SCHEMA,
                        self.instance_id,
                        manifest["created_at"],
                    ),
                )
            return BackupSetView(
                id=backup_id,
                state="COMPLETE",
                manifest_path=str(final_manifest),
                sha256=manifest_hash,
                schema_version=CURRENT_GATEWAY_SCHEMA,
                instance_id=self.instance_id,
                created_at=str(manifest["created_at"]),
            )
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise
