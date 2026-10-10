"""Backup validation and restore preview without mutating the live database."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from racp_domain.models import RACPError
from racp_protocol.management import RestorePreview

from racp_gateway.store import GatewayStore


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class RestoreManager:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def validate(self, backup_id: str) -> RestorePreview:
        row = self.store.db.execute(
            "SELECT * FROM backup_sets WHERE id=? AND state='COMPLETE'",
            (backup_id,),
        ).fetchone()
        if row is None:
            raise RACPError("INVALID_ARGUMENT", "backup is not complete or does not exist")
        manifest_path = Path(str(row["manifest_path"]))
        if not manifest_path.is_file() or _hash(manifest_path) != row["sha256"]:
            return RestorePreview(
                backup_id=backup_id,
                valid=False,
                schema_version=int(row["schema_version"]),
                instance_id=str(row["instance_id"]),
                database_integrity="failed",
                warnings=["backup manifest hash mismatch"],
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest["files"]
            for item in files:
                path = manifest_path.parent / item["path"]
                if not path.is_file() or _hash(path) != item["sha256"]:
                    raise ValueError("backup file hash mismatch")
            database = manifest_path.parent / "gateway.db"
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                connection.close()
            if integrity != "ok":
                raise ValueError("database integrity check failed")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error):
            return RestorePreview(
                backup_id=backup_id,
                valid=False,
                schema_version=int(row["schema_version"]),
                instance_id=str(row["instance_id"]),
                database_integrity="failed",
                warnings=["backup contents failed validation"],
            )
        return RestorePreview(
            backup_id=backup_id,
            valid=True,
            schema_version=int(manifest["schema_version"]),
            instance_id=str(manifest["instance_id"]),
            database_integrity="ok",
        )

    def restore_database(self, backup_id: str, destination: Path) -> Path:
        preview = self.validate(backup_id)
        if not preview.valid:
            raise RACPError("INVALID_ARGUMENT", "backup failed restore validation")
        row = self.store.db.execute(
            "SELECT manifest_path FROM backup_sets WHERE id=? AND state='COMPLETE'",
            (backup_id,),
        ).fetchone()
        assert row is not None
        manifest_path = Path(str(row["manifest_path"]))
        source = manifest_path.parent / "gateway.db"
        target = destination.resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".restore.tmp")
        if temporary.exists():
            temporary.unlink()
        shutil.copy2(source, temporary)
        check = sqlite3.connect(f"file:{temporary}?mode=ro", uri=True)
        try:
            if str(check.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                raise RACPError("INVALID_ARGUMENT", "restored database failed integrity check")
        finally:
            check.close()
        os.replace(temporary, target)
        return target

    def stage(self, backup_id: str, destination: Path) -> dict[str, str]:
        """Copy a validated backup into an isolated restore root and invalidate live credentials."""
        preview = self.validate(backup_id)
        if not preview.valid:
            raise RACPError("INVALID_ARGUMENT", "backup failed restore validation")
        row = self.store.db.execute(
            "SELECT manifest_path FROM backup_sets WHERE id=? AND state='COMPLETE'",
            (backup_id,),
        ).fetchone()
        assert row is not None
        manifest_path = Path(str(row["manifest_path"])).resolve(strict=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = destination.resolve(strict=False)
        probe = root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        required = sum(int(item.get("bytes", 0)) for item in manifest["files"]) + 16 * 1024 * 1024
        free = shutil.disk_usage(probe).free
        if free < required:
            raise RACPError(
                "RESOURCE_EXHAUSTED",
                "insufficient disk space for staged Gateway restore",
                required_bytes=required,
                free_bytes=free,
            )
        root.mkdir(parents=True, exist_ok=False)
        copied: dict[str, str] = {}
        try:
            for item in manifest["files"]:
                relative = Path(str(item["path"]))
                if relative.is_absolute() or ".." in relative.parts:
                    raise RACPError("INVALID_ARGUMENT", "backup contains an unsafe restore path")
                source = (manifest_path.parent / relative).resolve(strict=True)
                if not source.is_relative_to(manifest_path.parent.resolve()):
                    raise RACPError("INVALID_ARGUMENT", "backup restore path escapes its root")
                target = (root / relative).resolve(strict=False)
                if not target.is_relative_to(root):
                    raise RACPError("INVALID_ARGUMENT", "restore target escapes staging root")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if _hash(target) != item["sha256"]:
                    raise RACPError("CHECKSUM_MISMATCH", "staged restore file hash differs")
                copied[relative.as_posix()] = str(target)
            database = root / "gateway.db"
            connection = sqlite3.connect(database)
            try:
                tables = {
                    str(item[0])
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "console_sessions" in tables:
                    connection.execute("DELETE FROM console_sessions")
                if "console_setup" in tables:
                    connection.execute("DELETE FROM console_setup")
                if "credentials" in tables:
                    connection.execute("UPDATE credentials SET expires=0")
                if "enrollments" in tables:
                    connection.execute("UPDATE enrollments SET used=1")
                if "management_users" in tables:
                    connection.execute(
                        "UPDATE management_users SET auth_revision=auth_revision+1 WHERE active=1"
                    )
                if "devices" in tables:
                    connection.execute(
                        "UPDATE devices SET epoch=epoch+1,"
                        "info=json_set(info,'$.status','OFFLINE')"
                    )
                if "approvals" in tables:
                    connection.execute(
                        "UPDATE approvals SET state='EXPIRED' WHERE state NOT IN "
                        "('CONSUMED','DENIED','EXPIRED')"
                    )
                if "operations" in tables:
                    connection.execute(
                        "UPDATE operations SET state='UNKNOWN',result=NULL," 
                        "error=?,updated_at=datetime('now') WHERE state NOT IN "
                        "('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','UNKNOWN')",
                        (
                            json.dumps(
                                {
                                    "code": "EXECUTION_UNKNOWN",
                                    "message": "operation state invalidated by Gateway restore",
                                }
                            ),
                        ),
                    )
                connection.commit()
                if str(connection.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                    raise RACPError("INVALID_ARGUMENT", "staged restore database failed integrity")
            finally:
                connection.close()
            copied["gateway.db"] = str(database)
            return copied
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
