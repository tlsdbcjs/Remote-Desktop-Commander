"""Durable output attachments are independent of immutable execution outcomes."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import OutputDescriptor, new_id, timestamp
from racp_sdk.journal import Journal


class OutputSpool:
    def __init__(self, root: Path, journal: Journal) -> None:
        self.root, self.journal = root, journal
        self.quota_bytes = 10 * 1024**3
        self.reservations: dict[str, int] = {}
        self.external_usage: Callable[[], int] = lambda: 0
        journal.db.execute("""CREATE TABLE IF NOT EXISTS output_spool (
          id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, filename TEXT NOT NULL,
          size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
          artifact_id TEXT, transfer_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, error_code TEXT)""")
        journal.db.execute(
            "CREATE INDEX IF NOT EXISTS output_spool_operation ON output_spool(operation_id)"
        )
        journal.db.execute(
            "CREATE INDEX IF NOT EXISTS output_spool_pending "
            "ON output_spool(artifact_id,updated_at)"
        )

    def reserve(self, operation_id: str, operation: str, payload: dict[str, Any]) -> None:
        if operation in {
            "terminal.close",
            "process.terminate",
            "browser.close",
            "browser.close_page",
            "desktop.lease_release",
        }:
            return  # Resource cleanup remains available when output storage is full.
        pending = self.journal.db.execute(
            "SELECT COUNT(*) FROM output_spool WHERE artifact_id IS NULL"
        ).fetchone()[0]
        if pending + len(self.reservations) >= 64:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "Agent output spool entry limit reached", layer="agent"
            )
        # Reserve worst-case output plus binary input cache before a provider can mutate.
        amount = (
            72 * 1024**2
            if operation == "shell.exec"
            else 1024**3
            if operation == "filesystem.read"
            else 2 * int(payload.get("max_bytes", 1024**3))
            if operation == "browser.download"
            else 1024**3 + 64 * 1024**2
            if operation == "browser.upload"
            else 64 * 1024**2
        )
        if payload.get("artifact_id"):
            amount += 1024**3
        used = sum(
            item.stat().st_size
            for item in self.root.iterdir()
            if item.is_file() and item.name.partition(".")[0] not in self.reservations
        )
        used += self.external_usage()
        if used + sum(self.reservations.values()) + amount > self.quota_bytes:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "Agent output spool reservation exceeds quota", layer="agent"
            )
        self.reservations[operation_id] = amount

    def release(self, operation_id: str) -> None:
        self.reservations.pop(operation_id, None)

    def register(
        self, path: Path, operation_id: str, media_type: str, size: int, sha256: str
    ) -> dict[str, Any]:
        if path.parent.resolve() != self.root.resolve() or path.is_symlink():
            raise RACPError("PERMISSION_DENIED", "output is outside the Agent spool", layer="agent")
        output = OutputDescriptor.model_validate(
            {"id": new_id("output"), "size_bytes": size, "sha256": sha256, "media_type": media_type}
        )
        self.journal.db.execute(
            "INSERT INTO output_spool VALUES (?,?,?,?,?,?,NULL,NULL,?,?,0,NULL)",
            (
                output.id,
                operation_id,
                path.name,
                size,
                sha256,
                media_type,
                timestamp(),
                timestamp(),
            ),
        )
        return self.get(output.id)

    def get(self, output_id: str) -> dict[str, Any]:
        row = self.journal.db.execute(
            "SELECT * FROM output_spool WHERE id=?", (output_id,)
        ).fetchone()
        if row is None:
            raise RACPError("ARTIFACT_NOT_FOUND", "Agent output not found", layer="agent")
        return dict(row)

    def descriptors(self, operation_id: str) -> list[OutputDescriptor]:
        return [
            OutputDescriptor.model_validate(
                {
                    key: row[key]
                    for key in ("id", "size_bytes", "sha256", "media_type", "artifact_id")
                }
            )
            for row in self.journal.db.execute(
                "SELECT * FROM output_spool WHERE operation_id=? ORDER BY created_at LIMIT 4",
                (operation_id,),
            )
        ]

    def pending(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.journal.db.execute(
                "SELECT * FROM output_spool WHERE artifact_id IS NULL ORDER BY updated_at LIMIT 16"
            )
        ]

    def pinned_operations(self) -> set[str]:
        return {
            row[0]
            for row in self.journal.db.execute(
                "SELECT DISTINCT operation_id FROM output_spool WHERE artifact_id IS NULL"
            )
        }

    def collect_completed_files(self) -> None:
        for row in self.journal.db.execute(
            "SELECT filename FROM output_spool WHERE artifact_id IS NOT NULL"
        ):
            try:
                (self.root / row[0]).unlink(missing_ok=True)
            except OSError:
                pass

    def set_transfer(self, output_id: str, transfer_id: str) -> None:
        self.journal.db.execute(
            "UPDATE output_spool SET transfer_id=? WHERE id=?", (transfer_id, output_id)
        )

    def failed(self, output_id: str, code: str) -> None:
        self.journal.db.execute(
            "UPDATE output_spool SET attempts=attempts+1,error_code=?,updated_at=? WHERE id=?",
            (code, timestamp(), output_id),
        )

    def completed(self, output_id: str, artifact_id: str) -> None:
        self.journal.db.execute(
            "UPDATE output_spool SET artifact_id=?,error_code=NULL,updated_at=? WHERE id=?",
            (artifact_id, timestamp(), output_id),
        )
