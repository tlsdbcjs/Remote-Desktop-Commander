"""Bounded asynchronous management log exports with authenticated receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import BackgroundTasks
from racp_domain.models import RACPError
from racp_protocol.management import (
    LogExportCreateRequest,
    LogExportReceiptView,
    ManagementPrincipal,
)
from racp_sdk.security import canonical_digest

from racp_gateway.management.authorization import authorize_management
from racp_gateway.management.logs import LogStore
from racp_gateway.store import GatewayStore


class LogExportManager:
    def __init__(self, database: Path, output_dir: Path) -> None:
        self.database = database.resolve()
        self.output_dir = output_dir.resolve(strict=False)

    def _receipt_path(self, export_id: str) -> Path:
        if not re.fullmatch(r"lex_[0-9a-f]{32}", export_id):
            raise RACPError("INVALID_ARGUMENT", "invalid log export identifier")
        return self.output_dir / f"{export_id}.receipt.json"

    def _load_receipt(self, export_id: str) -> tuple[LogExportReceiptView, dict[str, object]]:
        receipt_path = self._receipt_path(export_id)
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise RACPError("NOT_FOUND", "log export was not found")
        document = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt = LogExportReceiptView.model_validate(document["receipt"])
        return receipt, document

    def _write_receipt(
        self,
        receipt: LogExportReceiptView,
        *,
        actor_id: str,
        realm_id: str,
    ) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        destination = self._receipt_path(receipt.id)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "receipt": receipt.model_dump(mode="json"),
                    "actor_id": actor_id,
                    "realm_id": realm_id,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)

    def create(
        self,
        principal: ManagementPrincipal,
        request: LogExportCreateRequest,
        background: BackgroundTasks,
    ) -> LogExportReceiptView:
        authorize_management(principal, "logs.export", request.query.device_id)
        identity = canonical_digest(
            {
                "realm": principal.realm_id,
                "actor": principal.actor_id,
                "idempotency_key": request.idempotency_key,
            }
        )
        export_id = f"lex_{identity[:32]}"
        receipt_path = self._receipt_path(export_id)
        if receipt_path.is_file() and not receipt_path.is_symlink():
            receipt, document = self._load_receipt(export_id)
            if document.get("actor_id") != principal.actor_id:
                raise RACPError("PERMISSION_DENIED", "log export belongs to another actor")
            return receipt
        created = datetime.now(UTC)
        suffix = "jsonl" if request.format == "jsonl" else "json"
        receipt = LogExportReceiptView(
            id=export_id,
            state="PENDING",
            format=request.format,
            file_name=f"racp-logs-{export_id}.{suffix}",
            rows=0,
            size_bytes=0,
            truncated=False,
            created_at=created.isoformat().replace("+00:00", "Z"),
            expires_at=(created + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        )
        self._write_receipt(
            receipt,
            actor_id=principal.actor_id,
            realm_id=principal.realm_id,
        )
        background.add_task(
            self._run,
            principal.model_dump(mode="json"),
            request.model_dump(mode="json"),
            receipt.model_dump(mode="json"),
        )
        return receipt

    def status(
        self,
        principal: ManagementPrincipal,
        export_id: str,
    ) -> LogExportReceiptView:
        authorize_management(principal, "logs.export")
        receipt, document = self._load_receipt(export_id)
        if document.get("realm_id") != principal.realm_id or document.get(
            "actor_id"
        ) != principal.actor_id:
            raise RACPError("PERMISSION_DENIED", "log export belongs to another principal")
        return receipt

    def download(
        self,
        principal: ManagementPrincipal,
        export_id: str,
    ) -> tuple[LogExportReceiptView, Path]:
        receipt = self.status(principal, export_id)
        if receipt.state != "SUCCEEDED":
            raise RACPError("CONFLICT", "log export is not ready")
        expires = datetime.fromisoformat(receipt.expires_at.replace("Z", "+00:00"))
        if expires <= datetime.now(UTC):
            raise RACPError("NOT_FOUND", "log export expired")
        path = self.output_dir / receipt.file_name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != receipt.size_bytes:
            raise RACPError("NOT_FOUND", "log export file is unavailable")
        if hashlib.sha256(path.read_bytes()).hexdigest() != receipt.sha256:
            raise RACPError("CHECKSUM_MISMATCH", "log export file failed integrity verification")
        return receipt, path

    def _run(
        self,
        principal_document: dict[str, object],
        request_document: dict[str, object],
        receipt_document: dict[str, object],
    ) -> None:
        principal = ManagementPrincipal.model_validate(principal_document)
        request = LogExportCreateRequest.model_validate(request_document)
        receipt = LogExportReceiptView.model_validate(receipt_document)
        worker: GatewayStore | None = None
        try:
            worker = GatewayStore(self.database)
            logs = LogStore(worker)
            encoded_rows: list[bytes] = []
            row_count = 0
            truncated = False
            cursor: str | None = None
            page_size = min(500, request.max_rows)
            while row_count < request.max_rows:
                query = request.query.model_copy(
                    update={"limit": page_size, "cursor": cursor}
                )
                page = logs.search(principal, query)
                for item in page.items:
                    if row_count >= request.max_rows:
                        truncated = True
                        break
                    encoded = json.dumps(
                        item.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                    if request.format == "jsonl":
                        projected = sum(len(value) + 1 for value in encoded_rows) + len(encoded) + 1
                    else:
                        projected = (
                            sum(len(value) for value in encoded_rows)
                            + len(encoded)
                            + len(encoded_rows)
                            + 3
                        )
                    if projected > request.max_bytes:
                        truncated = True
                        break
                    encoded_rows.append(encoded)
                    row_count += 1
                if truncated:
                    break
                cursor = page.next_cursor
                if cursor is None:
                    break
            if request.format == "json":
                payload = b"[" + b",".join(encoded_rows) + b"]\n"
            else:
                payload = b"".join(value + b"\n" for value in encoded_rows)
            destination = self.output_dir / receipt.file_name
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            os.replace(temporary, destination)
            complete = receipt.model_copy(
                update={
                    "state": "SUCCEEDED",
                    "rows": row_count,
                    "size_bytes": len(payload),
                    "truncated": truncated or cursor is not None,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            self._write_receipt(
                complete,
                actor_id=principal.actor_id,
                realm_id=principal.realm_id,
            )
        except Exception:
            failed = receipt.model_copy(
                update={"state": "FAILED", "error": "log export generation failed"}
            )
            self._write_receipt(
                failed,
                actor_id=principal.actor_id,
                realm_id=principal.realm_id,
            )
        finally:
            if worker is not None:
                worker.close()
