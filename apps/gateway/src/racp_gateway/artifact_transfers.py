import asyncio
import hashlib
import os
import re
import struct
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from racp_domain.models import TERMINAL_STATES, RACPError
from racp_protocol.artifacts import (
    ARTIFACT_INPUT_OPERATIONS,
    CHUNK_BYTES,
    MAX_ARTIFACT_BYTES,
    TransferComplete,
    TransferCreate,
)
from racp_protocol.models import new_id, timestamp
from racp_sdk.security import digest, token
from starlette.background import BackgroundTask

from racp_gateway.store import GatewayStore

ACTIVE_OPERATIONS = {"DISPATCHED", "RUNNING", "CANCEL_REQUESTED", "RECONCILING"}


def utc_time(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


class ArtifactManager:
    def __init__(self, root: Path, store: GatewayStore, *, quota_bytes: int = 10 * 1024**3) -> None:
        self.root, self.store, self.quota_bytes = root, store, quota_bytes
        self.partial = root / ".transfers"
        self.partial.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.locks: dict[str, asyncio.Lock] = {}
        self.publish_lock = asyncio.Lock()
        self.ingress_slots = asyncio.Semaphore(4)
        self.readers: dict[str, set[str]] = {}
        store.db.execute("""CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, device_id TEXT NOT NULL,
            owner_id TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL, media_type TEXT NOT NULL)""")
        columns = {row[1] for row in store.db.execute("PRAGMA table_info(artifacts)")}
        if "media_type" not in columns:
            store.db.execute(
                "ALTER TABLE artifacts ADD COLUMN media_type TEXT NOT NULL "
                "DEFAULT 'application/octet-stream'"
            )
        if "state" not in columns:
            store.db.execute("ALTER TABLE artifacts ADD COLUMN state TEXT NOT NULL DEFAULT 'READY'")
        if "expires" not in columns:
            store.db.execute("ALTER TABLE artifacts ADD COLUMN expires REAL NOT NULL DEFAULT 0")
            store.db.execute(
                "UPDATE artifacts SET expires=unixepoch(created_at)+? WHERE expires=0",
                (7 * 86400,),
            )
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS operation_outputs (
              id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, device_id TEXT NOT NULL,
              owner_id TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
              media_type TEXT NOT NULL, artifact_id TEXT, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifact_transfers (
              id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              device_id TEXT NOT NULL, operation_id TEXT NOT NULL, direction TEXT NOT NULL,
              size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, credential_digest TEXT NOT NULL,
              credential_expires REAL NOT NULL, committed_bytes INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS artifact_chunks (
              transfer_id TEXT NOT NULL, offset_bytes INTEGER NOT NULL, size_bytes INTEGER NOT NULL,
              sha256 TEXT NOT NULL, PRIMARY KEY(transfer_id,offset_bytes));
        """)
        transfer_columns = {
            row[1] for row in store.db.execute("PRAGMA table_info(artifact_transfers)")
        }
        if "output_id" not in transfer_columns:
            store.db.execute("ALTER TABLE artifact_transfers ADD COLUMN output_id TEXT")
        for row in store.db.execute(
            "SELECT * FROM artifact_transfers WHERE direction='upload' "
            "AND state IN ('UPLOADING','VERIFYING')"
        ).fetchall():
            path = self.partial / row["id"]
            if path.exists() and path.stat().st_size >= row["committed_bytes"]:
                with path.open("r+b") as stream:
                    stream.truncate(row["committed_bytes"])
                store.db.execute(
                    "UPDATE artifact_transfers SET state='UPLOADING' WHERE id=?", (row["id"],)
                )
                store.db.execute(
                    "UPDATE artifacts SET state='UPLOADING' WHERE id=?", (row["artifact_id"],)
                )
            else:
                self.fail(row["id"], "missing committed bytes")

    def get(self, artifact_id: str, owner: str) -> dict[str, Any]:
        row = self.store.db.execute(
            "SELECT * FROM artifacts WHERE id=? AND owner_id=?", (artifact_id, owner)
        ).fetchone()
        if row is None:
            raise RACPError("ARTIFACT_NOT_FOUND", "artifact was not found")
        result = dict(row)
        result["expires_at"] = utc_time(result.pop("expires"))
        return result

    def ready(self, artifact_id: str, owner: str) -> dict[str, Any]:
        record = self.get(artifact_id, owner)
        expires = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00")).timestamp()
        if (
            record["state"] in {"EXPIRED", "DELETED"}
            or expires <= time.time()
            and not self.pinned(artifact_id)
        ):
            raise RACPError("ARTIFACT_EXPIRED", "artifact expired")
        if record["state"] != "READY":
            raise RACPError("CONFLICT", "artifact is not ready", state=record["state"])
        return record

    async def png_preview(self, artifact_id: str, owner: str, operation_id: str) -> bytes | None:
        record = self.ready(artifact_id, owner)
        if record.get("operation_id") != operation_id:
            raise RACPError("PERMISSION_DENIED", "preview operation scope mismatch")
        if record["media_type"] != "image/png" or record["size_bytes"] > 2 * 1024 * 1024:
            return None
        lease = uuid.uuid4().hex
        self.readers.setdefault(artifact_id, set()).add(lease)

        def read() -> bytes:
            with (self.root / record["sha256"]).open("rb") as stream:
                return bytes(stream.read(2 * 1024 * 1024 + 1))

        reading = asyncio.create_task(asyncio.to_thread(read))
        try:
            try:
                data = await asyncio.shield(reading)
            except asyncio.CancelledError:
                while not reading.done():
                    try:
                        await asyncio.shield(reading)
                    except asyncio.CancelledError:
                        continue
                await reading
                raise
            if (
                len(data) != record["size_bytes"]
                or not data.startswith(b"\x89PNG\r\n\x1a\n")
                or hashlib.sha256(data).hexdigest() != record["sha256"]
            ):
                raise RACPError("PRECONDITION_FAILED", "preview hash mismatch")
            if len(data) < 33 or data[8:16] != b"\0\0\0\rIHDR":
                raise RACPError("PRECONDITION_FAILED", "invalid preview PNG header")
            width, height = struct.unpack(">II", data[16:24])
            if not 0 < width <= 1600 or not 0 < height <= 1600:
                return None
            return data
        finally:
            leases = self.readers.get(artifact_id)
            if leases is not None:
                leases.discard(lease)
                if not leases:
                    self.readers.pop(artifact_id, None)

    def operation_scope(self, operation_id: str, device_id: str) -> dict[str, Any]:
        operation = self.store.get(operation_id)
        if operation["device_id"] != device_id or operation["state"] not in ACTIVE_OPERATIONS:
            raise RACPError(
                "PERMISSION_DENIED", "transfer does not match an active device operation"
            )
        return operation

    def create(
        self, input: TransferCreate, *, owner: str | None = None, device: str | None = None
    ) -> dict[str, Any]:
        operation_id = input.operation_id or new_id("upload")
        if device is not None:
            if input.device_id != device or input.operation_id is None:
                raise RACPError("PERMISSION_DENIED", "device transfer scope denied")
            operation = self.store.get(operation_id)
            if input.output_id and operation["state"] in TERMINAL_STATES:
                self.assigned_output(input, operation, device)
            else:
                operation = self.operation_scope(operation_id, device)
            owner = operation["request"]["context"]["principal_id"]
            if input.direction == "download" and (
                operation["request"]["operation"] not in ARTIFACT_INPUT_OPERATIONS
                or operation["request"]["payload"].get("artifact_id") != input.artifact_id
            ):
                raise RACPError("PERMISSION_DENIED", "download is not assigned to this operation")
        assert owner is not None
        enrolled = self.store.device(input.device_id, owner)
        if enrolled["revoked"]:
            raise RACPError("DEVICE_REVOKED", "device was revoked")
        if input.operation_id and device is None:
            operation = self.store.get(operation_id)
            if (
                operation["device_id"] != input.device_id
                or operation["request"]["context"]["principal_id"] != owner
            ):
                raise RACPError("PERMISSION_DENIED", "operation scope denied")
        now = time.time()
        credential, transfer_id = token(), new_id("trf")
        with self.store.transaction():
            self.check_capacity(input.device_id, now)
            if input.direction == "upload":
                size, sha256 = input.size_bytes, input.sha256
                assert size is not None and sha256 is not None
                used = self.store.db.execute(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM artifacts WHERE state='READY'"
                ).fetchone()[0]
                reserved = self.store.db.execute(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM artifact_transfers "
                    "WHERE direction='upload' AND state IN ('UPLOADING','VERIFYING')"
                ).fetchone()[0]
                if used + reserved + size > self.quota_bytes:
                    raise RACPError("RESOURCE_EXHAUSTED", "artifact reservation exceeds quota")
                artifact_id = new_id("art")
                self.store.db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        artifact_id,
                        operation_id,
                        input.device_id,
                        owner,
                        sha256,
                        size,
                        timestamp(),
                        input.media_type,
                        "UPLOADING",
                        now + 7 * 86400,
                    ),
                )
                try:
                    fd = os.open(
                        self.partial / transfer_id, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                    )
                    os.close(fd)
                except OSError as exc:
                    raise RACPError(
                        "RESOURCE_EXHAUSTED", "artifact storage allocation failed"
                    ) from exc
                state = "UPLOADING"
            else:
                assert input.artifact_id is not None
                artifact = self.ready(input.artifact_id, owner)
                artifact_id, size, sha256 = (
                    artifact["id"],
                    artifact["size_bytes"],
                    artifact["sha256"],
                )
                state = "DOWNLOAD"
            self.store.db.execute(
                "INSERT INTO artifact_transfers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transfer_id,
                    artifact_id,
                    owner,
                    input.device_id,
                    operation_id,
                    input.direction,
                    size,
                    sha256,
                    digest(credential),
                    now + 600,
                    0,
                    state,
                    now,
                    now,
                    input.output_id,
                ),
            )
            self.store.audit(
                "artifact_transfer_created",
                {
                    "device_id": input.device_id,
                    "operation_id": operation_id,
                    "operation": "artifact." + input.direction,
                },
                transfer_id=transfer_id,
                artifact_id=artifact_id,
                size_bytes=size,
            )
        return {**self.status(transfer_id), "credential": credential}

    def assigned_output(
        self, input: TransferCreate, operation: dict[str, Any], device: str
    ) -> None:
        row = self.store.db.execute(
            "SELECT * FROM operation_outputs WHERE id=? AND operation_id=? AND device_id=?",
            (input.output_id, operation["id"], device),
        ).fetchone()
        if (
            row is None
            or (row["size_bytes"], row["sha256"], row["media_type"])
            != (input.size_bytes, input.sha256, input.media_type)
            or operation["device_id"] != device
        ):
            raise RACPError("PERMISSION_DENIED", "upload differs from assigned completed output")

    def observe_outputs(self, outputs: list[dict[str, Any]], operation: dict[str, Any]) -> None:
        owner = operation["request"]["context"]["principal_id"]
        with self.store.transaction():
            for output in outputs:
                row = self.store.db.execute(
                    "SELECT * FROM operation_outputs WHERE id=?", (output["id"],)
                ).fetchone()
                identity = (
                    operation["id"],
                    operation["device_id"],
                    owner,
                    output["size_bytes"],
                    output["sha256"],
                    output["media_type"],
                )
                if (
                    row
                    and tuple(
                        row[key]
                        for key in (
                            "operation_id",
                            "device_id",
                            "owner_id",
                            "size_bytes",
                            "sha256",
                            "media_type",
                        )
                    )
                    != identity
                ):
                    raise RACPError("CONFLICT", "output descriptor cannot change scope")
                artifact_id = output.get("artifact_id")
                if row and row["artifact_id"]:
                    if artifact_id and artifact_id != row["artifact_id"]:
                        raise RACPError("CONFLICT", "completed output Artifact cannot change")
                    continue
                if artifact_id:
                    artifact = self.get(artifact_id, owner)
                    if (
                        artifact["state"] != "READY"
                        or tuple(
                            artifact[key]
                            for key in (
                                "operation_id",
                                "device_id",
                                "owner_id",
                                "size_bytes",
                                "sha256",
                                "media_type",
                            )
                        )
                        != identity
                    ):
                        raise RACPError("PERMISSION_DENIED", "output Artifact differs from scope")
                if row is None:
                    count = self.store.db.execute(
                        "SELECT COUNT(*) FROM operation_outputs WHERE operation_id=?",
                        (operation["id"],),
                    ).fetchone()[0]
                    if count >= 4:
                        raise RACPError("RESOURCE_EXHAUSTED", "operation output limit reached")
                    self.store.db.execute(
                        "INSERT INTO operation_outputs VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (output["id"], *identity, artifact_id, timestamp(), timestamp()),
                    )
                elif artifact_id:
                    self.store.db.execute(
                        "UPDATE operation_outputs SET artifact_id=?,updated_at=? WHERE id=?",
                        (artifact_id, timestamp(), output["id"]),
                    )
                self.store.audit(
                    "operation_output_observed",
                    operation["request"],
                    output_id=output["id"],
                    artifact_id=artifact_id,
                )

    def outputs(self, operation_id: str, owner: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.store.db.execute(
                "SELECT * FROM operation_outputs WHERE operation_id=? AND owner_id=? "
                "ORDER BY created_at",
                (operation_id, owner),
            )
        ]

    def check_capacity(self, device_id: str, now: float, *, exclude: str = "") -> None:
        row = self.store.db.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(device_id=?),0) AS device "
            "FROM artifact_transfers WHERE state IN ('UPLOADING','VERIFYING','DOWNLOAD') "
            "AND credential_expires>? AND id<>?",
            (device_id, now, exclude),
        ).fetchone()
        if row["device"] >= 2 or row["total"] >= 64:
            raise RACPError(
                "RESOURCE_EXHAUSTED",
                "active transfer limit reached",
                device_limit=2,
                global_limit=64,
                retry_after_ms=1000,
            )

    def transfer(self, transfer_id: str) -> dict[str, Any]:
        row = self.store.db.execute(
            "SELECT * FROM artifact_transfers WHERE id=?", (transfer_id,)
        ).fetchone()
        if row is None:
            raise RACPError("ARTIFACT_NOT_FOUND", "transfer was not found")
        return dict(row)

    def status(self, transfer_id: str) -> dict[str, Any]:
        result = self.transfer(transfer_id)
        result.pop("credential_digest")
        result["credential_expires_at"] = utc_time(result.pop("credential_expires"))
        result["committed_bytes"] = str(result["committed_bytes"])
        result["chunk_bytes"] = CHUNK_BYTES
        result["created_at"] = utc_time(result.pop("created"))
        result["updated_at"] = utc_time(result.pop("updated"))
        return result

    def authorize_identity(
        self, transfer_id: str, *, owner: str | None = None, device: str | None = None
    ) -> dict[str, Any]:
        record = self.transfer(transfer_id)
        if owner != record["owner_id"] and device != record["device_id"]:
            raise RACPError("PERMISSION_DENIED", "transfer belongs to another identity")
        if self.store.device(record["device_id"], record["owner_id"])["revoked"]:
            raise RACPError("DEVICE_REVOKED", "device was revoked")
        if device is not None and owner is None:
            try:
                operation = self.store.get(record["operation_id"])
            except RACPError as exc:
                raise RACPError(
                    "PERMISSION_DENIED", "transfer is not assigned to a Device operation"
                ) from exc
            if operation["device_id"] != device:
                raise RACPError("PERMISSION_DENIED", "transfer operation belongs to another Device")
            if operation["state"] in TERMINAL_STATES:
                self.assigned_output(
                    TransferCreate.model_validate(
                        {
                            "device_id": device,
                            "operation_id": operation["id"],
                            "output_id": record["output_id"],
                            "size_bytes": record["size_bytes"],
                            "sha256": record["sha256"],
                            "media_type": self.get(record["artifact_id"], record["owner_id"])[
                                "media_type"
                            ],
                        }
                    ),
                    operation,
                    device,
                )
        return record

    def scoped(
        self, transfer_id: str, credential: str, direction: str | None = None
    ) -> dict[str, Any]:
        record = self.transfer(transfer_id)
        if record["credential_digest"] != digest(credential):
            raise RACPError("UNAUTHENTICATED", "invalid transfer credential")
        if record["credential_expires"] <= time.time():
            raise RACPError(
                "ARTIFACT_EXPIRED", "transfer credential expired; reauthorize the same scope"
            )
        if direction and direction != record["direction"]:
            raise RACPError("PERMISSION_DENIED", "transfer direction does not match")
        if direction == "download" and record["state"] == "DOWNLOAD_COMPLETE":
            raise RACPError("CONFLICT", "completed download must be reauthorized before resuming")
        if self.store.device(record["device_id"], record["owner_id"])["revoked"]:
            raise RACPError("DEVICE_REVOKED", "device was revoked")
        return record

    def renew(
        self, transfer_id: str, *, owner: str | None = None, device: str | None = None
    ) -> dict[str, Any]:
        record = self.authorize_identity(transfer_id, owner=owner, device=device)
        if record["state"] in {"FAILED", "EXPIRED"}:
            raise RACPError("ARTIFACT_EXPIRED", "transfer cannot be renewed")
        credential = token()
        now = time.time()
        with self.store.transaction():
            if record["state"] in {"UPLOADING", "VERIFYING", "DOWNLOAD", "DOWNLOAD_COMPLETE"}:
                self.check_capacity(record["device_id"], now, exclude=transfer_id)
            self.store.db.execute(
                "UPDATE artifact_transfers SET credential_digest=?,credential_expires=?,updated=?,"
                "state=CASE WHEN state='DOWNLOAD_COMPLETE' THEN 'DOWNLOAD' ELSE state END "
                "WHERE id=?",
                (digest(credential), now + 600, now, transfer_id),
            )
        return {**self.status(transfer_id), "credential": credential}

    async def put(self, transfer_id: str, credential: str, request: Request) -> dict[str, Any]:
        self.scoped(transfer_id, credential, "upload")
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", request.headers.get("content-range", ""))
        if not match:
            raise RACPError("INVALID_ARGUMENT", "upload requires a single Content-Range")
        offset, end, total = map(int, match.groups())
        if end < offset or end - offset + 1 > CHUNK_BYTES or end >= total:
            raise RACPError("INVALID_ARGUMENT", "chunk range is invalid")
        async with self.ingress_slots:
            chunk = bytearray()
            async for piece in request.stream():
                if len(chunk) + len(piece) > CHUNK_BYTES:
                    raise RACPError("INVALID_ARGUMENT", "chunk exceeds 4 MiB")
                chunk.extend(piece)
            return await self.put_bytes(
                transfer_id,
                credential,
                offset,
                total,
                bytes(chunk),
                request.headers.get("x-chunk-sha256", ""),
                expected_end=end,
            )

    async def put_bytes(
        self,
        transfer_id: str,
        credential: str,
        offset: int,
        total: int,
        chunk: bytes,
        sha256: str,
        *,
        expected_end: int | None = None,
    ) -> dict[str, Any]:
        if not chunk or len(chunk) > CHUNK_BYTES or offset < 0 or offset + len(chunk) > total:
            raise RACPError("INVALID_ARGUMENT", "invalid chunk size/offset")
        if expected_end is not None and offset + len(chunk) - 1 != expected_end:
            raise RACPError("INVALID_ARGUMENT", "body size does not match Content-Range")
        if hashlib.sha256(chunk).hexdigest() != sha256:
            raise RACPError("CHECKSUM_MISMATCH", "chunk checksum differs")
        async with self.locks.setdefault(transfer_id, asyncio.Lock()):
            record = self.scoped(transfer_id, credential, "upload")
            if total != record["size_bytes"]:
                raise RACPError("CONFLICT", "declared total differs from transfer scope")
            previous = self.store.db.execute(
                "SELECT * FROM artifact_chunks WHERE transfer_id=? AND offset_bytes=?",
                (transfer_id, offset),
            ).fetchone()
            if previous:
                if previous["sha256"] != sha256 or previous["size_bytes"] != len(chunk):
                    raise RACPError("CONFLICT", "different data at a committed chunk offset")
                return self.status(transfer_id)
            if record["state"] != "UPLOADING" or offset != record["committed_bytes"]:
                raise RACPError(
                    "CONFLICT",
                    "chunk must begin at the committed offset",
                    committed_bytes=str(record["committed_bytes"]),
                )
            path = self.partial / transfer_id

            def append() -> None:
                with path.open("r+b") as stream:
                    stream.truncate(offset)
                    stream.seek(offset)
                    stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())

            try:
                work = asyncio.create_task(asyncio.to_thread(append))
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    await asyncio.shield(work)
                self.scoped(transfer_id, credential, "upload")
                with self.store.transaction():
                    self.store.db.execute(
                        "INSERT INTO artifact_chunks VALUES (?,?,?,?)",
                        (transfer_id, offset, len(chunk), sha256),
                    )
                    self.store.db.execute(
                        "UPDATE artifact_transfers SET committed_bytes=?,updated=? WHERE id=?",
                        (offset + len(chunk), time.time(), transfer_id),
                    )
            except OSError as exc:
                self.fail(transfer_id, "storage write failed")
                raise RACPError("RESOURCE_EXHAUSTED", "artifact storage write failed") from exc
            return self.status(transfer_id)

    def fail(self, transfer_id: str, reason: str) -> None:
        record = self.transfer(transfer_id)
        if record["state"] == "READY":
            return
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE artifact_transfers SET state='FAILED' WHERE id=?", (transfer_id,)
            )
            if record["direction"] == "upload":
                self.store.db.execute(
                    "UPDATE artifacts SET state='FAILED' WHERE id=?", (record["artifact_id"],)
                )
            self.store.audit(
                "artifact_transfer_failed",
                {
                    "device_id": record["device_id"],
                    "operation_id": record["operation_id"],
                    "operation": "artifact." + record["direction"],
                },
                reason=reason,
            )
        try:
            (self.partial / transfer_id).unlink(missing_ok=True)
        except OSError:
            # A failed deletion stays eligible for the next collector pass.
            pass

    async def complete(
        self, transfer_id: str, credential: str, input: TransferComplete
    ) -> dict[str, Any]:
        work = asyncio.create_task(self._complete(transfer_id, credential, input))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # Finish verification/publication before releasing its locks. A disconnect
            # may lose the response; the same transfer ID safely retrieves the result.
            return await asyncio.shield(work)

    async def _complete(
        self, transfer_id: str, credential: str, input: TransferComplete
    ) -> dict[str, Any]:
        record = self.scoped(transfer_id, credential)
        if record["direction"] == "download":
            if (input.size_bytes, input.sha256) != (record["size_bytes"], record["sha256"]):
                raise RACPError("CHECKSUM_MISMATCH", "download acknowledgement differs from scope")
            if record["state"] == "DOWNLOAD_COMPLETE":
                return self.status(transfer_id)
            with self.store.transaction():
                self.store.db.execute(
                    "UPDATE artifact_transfers SET state='DOWNLOAD_COMPLETE',committed_bytes=?,"
                    "updated=? WHERE id=?",
                    (input.size_bytes, time.time(), transfer_id),
                )
                self.store.audit(
                    "artifact_download_acknowledged",
                    {
                        "device_id": record["device_id"],
                        "operation_id": record["operation_id"],
                        "operation": "artifact.download",
                    },
                    transfer_id=transfer_id,
                )
            return self.status(transfer_id)
        async with self.locks.setdefault(transfer_id, asyncio.Lock()):
            record = self.scoped(transfer_id, credential, "upload")
            if input.size_bytes != record["size_bytes"] or input.sha256 != record["sha256"]:
                raise RACPError("CONFLICT", "completion does not match transfer scope")
            if record["state"] == "READY":
                return self.get(record["artifact_id"], record["owner_id"])
            if record["state"] != "UPLOADING" or record["committed_bytes"] != record["size_bytes"]:
                raise RACPError("CONFLICT", "transfer is not fully committed")
            path = self.partial / transfer_id
            self.store.db.execute(
                "UPDATE artifact_transfers SET state='VERIFYING' WHERE id=?", (transfer_id,)
            )
            self.store.db.execute(
                "UPDATE artifacts SET state='VERIFYING' WHERE id=?", (record["artifact_id"],)
            )

            def verify() -> tuple[int, str]:
                with path.open("rb") as stream:
                    return os.fstat(stream.fileno()).st_size, hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()

            try:
                size, actual = await asyncio.to_thread(verify)
                if size != record["size_bytes"] or actual != record["sha256"]:
                    self.fail(transfer_id, "content checksum differs")
                    raise RACPError("CHECKSUM_MISMATCH", "complete content hash/size differs")
                async with self.publish_lock:
                    self.scoped(transfer_id, credential, "upload")
                    target = self.root / actual
                    if await asyncio.to_thread(target.exists):

                        def verify_existing() -> bool:
                            with target.open("rb") as stream:
                                return (
                                    os.fstat(stream.fileno()).st_size == size
                                    and hashlib.file_digest(stream, "sha256").hexdigest() == actual
                                )

                        if not await asyncio.to_thread(verify_existing):
                            self.fail(transfer_id, "existing blob is corrupt")
                            raise RACPError(
                                "INTERNAL_ERROR", "existing READY blob integrity failed"
                            )
                    else:
                        await asyncio.to_thread(os.link, path, target)
                    with self.store.transaction():
                        self.store.db.execute(
                            "UPDATE artifacts SET state='READY' WHERE id=?",
                            (record["artifact_id"],),
                        )
                        self.store.db.execute(
                            "UPDATE artifact_transfers SET state='READY',updated=? WHERE id=?",
                            (time.time(), transfer_id),
                        )
                        self.store.audit(
                            "artifact_ready",
                            {
                                "device_id": record["device_id"],
                                "operation_id": record["operation_id"],
                                "operation": "artifact.upload",
                            },
                            artifact_id=record["artifact_id"],
                            size_bytes=size,
                            sha256=actual,
                        )
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                return self.get(record["artifact_id"], record["owner_id"])
            except OSError as exc:
                self.fail(transfer_id, "content publication failed")
                raise RACPError("RESOURCE_EXHAUSTED", "artifact publication failed") from exc
            except asyncio.CancelledError:
                self.store.db.execute(
                    "UPDATE artifact_transfers SET state='UPLOADING' "
                    "WHERE id=? AND state='VERIFYING'",
                    (transfer_id,),
                )
                self.store.db.execute(
                    "UPDATE artifacts SET state='UPLOADING' WHERE id=? AND state='VERIFYING'",
                    (record["artifact_id"],),
                )
                raise

    def pinned(self, artifact_id: str) -> bool:
        if self.readers.get(artifact_id):
            return True
        if self.store.db.execute(
            "SELECT 1 FROM artifact_transfers WHERE artifact_id=? AND direction='download' "
            "AND state='DOWNLOAD' AND credential_expires>? LIMIT 1",
            (artifact_id, time.time()),
        ).fetchone():
            return True
        if self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='operation_admission'"
        ).fetchone():
            queued_reference = self.store.db.execute(
                "SELECT 1 FROM operation_admission AS a "
                "JOIN operations AS o ON o.id=a.operation_id "
                "WHERE a.state IN ('QUEUED','SENT') AND "
                "(json_extract(o.request,'$.payload.artifact_id')=? OR "
                "o.id=(SELECT operation_id FROM artifacts WHERE id=?)) LIMIT 1",
                (artifact_id, artifact_id),
            ).fetchone()
            if queued_reference:
                return True
        return (
            self.store.db.execute(
                "SELECT 1 FROM operations WHERE state IN "
                "('DISPATCHED','RUNNING','CANCEL_REQUESTED','RECONCILING') AND "
                "(json_extract(request,'$.payload.artifact_id')=? "
                "OR id=(SELECT operation_id FROM artifacts WHERE id=?)) LIMIT 1",
                (artifact_id, artifact_id),
            ).fetchone()
            is not None
        )

    def response(
        self,
        artifact_id: str,
        owner: str,
        request: Request,
        *,
        authorize: Callable[[], Any] | None = None,
    ) -> Response:
        record = self.ready(artifact_id, owner)
        etag = '"' + record["sha256"] + '"'
        size = record["size_bytes"]
        start, end, status = 0, size - 1, 200
        headers = {
            "ETag": etag,
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'attachment; filename="{artifact_id}.bin"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        if request.headers.get("if-none-match") == etag and not request.headers.get("range"):
            return Response(status_code=304, headers=headers)
        value = request.headers.get("range")
        if value and request.headers.get("if-range", etag) == etag:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
            if not match or not any(match.groups()) or size == 0:
                return Response(
                    status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
                )
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                suffix = int(last)
                start = max(0, size - suffix) if suffix else size
            if start >= size or end < start:
                return Response(
                    status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
                )
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(max(0, end - start + 1))
        lease = uuid.uuid4().hex
        stream = (self.root / record["sha256"]).open("rb")
        stream.seek(start)
        self.readers.setdefault(artifact_id, set()).add(lease)

        def release() -> None:
            stream.close()
            leases = self.readers.get(artifact_id)
            if leases is not None:
                leases.discard(lease)
                if not leases:
                    self.readers.pop(artifact_id, None)

        async def chunks() -> AsyncIterator[bytes]:
            remaining = end - start + 1
            try:
                while remaining > 0:
                    if authorize is not None:
                        authorize()
                    block = await asyncio.to_thread(stream.read, min(65536, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block
                    await asyncio.sleep(0)
            finally:
                release()

        async def cleanup() -> None:
            release()

        return StreamingResponse(
            chunks(),
            status_code=status,
            headers=headers,
            media_type="application/octet-stream",
            background=BackgroundTask(cleanup),
        )

    async def collect(self) -> dict[str, int]:
        expired, deleted, incomplete = 0, 0, 0
        async with self.publish_lock:
            now = time.time()
            for row in self.store.db.execute(
                "SELECT * FROM artifact_transfers WHERE direction='upload' "
                "AND state IN ('UPLOADING','VERIFYING') AND updated<?",
                (now - 3600,),
            ).fetchall():
                if self.locks.setdefault(row["id"], asyncio.Lock()).locked():
                    continue
                self.fail(row["id"], "incomplete transfer retention expired")
                incomplete += 1
            for row in self.store.db.execute(
                "SELECT * FROM artifacts WHERE state IN ('READY','EXPIRED') AND expires<=?", (now,)
            ).fetchall():
                if self.pinned(row["id"]):
                    continue
                self.store.db.execute(
                    "UPDATE artifacts SET state='EXPIRED' WHERE id=?", (row["id"],)
                )
                expired += 1
                shared = self.store.db.execute(
                    "SELECT 1 FROM artifacts WHERE sha256=? AND state='READY' AND id!=? LIMIT 1",
                    (row["sha256"], row["id"]),
                ).fetchone()
                if not shared:
                    try:
                        await asyncio.to_thread((self.root / row["sha256"]).unlink, missing_ok=True)
                    except OSError:
                        continue
                self.store.db.execute(
                    "UPDATE artifacts SET state='DELETED' WHERE id=?", (row["id"],)
                )
                deleted += 1
            # Only exact generated names are eligible. Never traverse outside the
            # artifact directories or remove a blob still referenced by live state.
            for path in self.partial.iterdir():
                if not re.fullmatch(r"trf_[0-9a-f]+", path.name) or path.is_symlink():
                    continue
                transfer = self.store.db.execute(
                    "SELECT state FROM artifact_transfers WHERE id=?", (path.name,)
                ).fetchone()
                if transfer is None or transfer["state"] not in {"UPLOADING", "VERIFYING"}:
                    try:
                        await asyncio.to_thread(path.unlink, missing_ok=True)
                    except OSError:
                        pass
            for path in self.root.iterdir():
                if not re.fullmatch(r"[0-9a-f]{64}", path.name) or path.is_symlink():
                    continue
                referenced = self.store.db.execute(
                    "SELECT 1 FROM artifacts WHERE sha256=? AND state IN "
                    "('READY','UPLOADING','VERIFYING','EXPIRED') LIMIT 1",
                    (path.name,),
                ).fetchone()
                if referenced is None:
                    try:
                        await asyncio.to_thread(path.unlink, missing_ok=True)
                    except OSError:
                        pass
        return {"expired": expired, "deleted": deleted, "incomplete_cleaned": incomplete}

    async def upload(
        self, request: Request, operation_id: str, device_id: str, expected_hash: str
    ) -> dict[str, Any]:
        length = request.headers.get("x-content-size", request.headers.get("content-length", ""))
        if not length.isdecimal() or int(length) > MAX_ARTIFACT_BYTES:
            raise RACPError("INVALID_ARGUMENT", "legacy ingress requires bounded content size")
        created = self.create(
            TransferCreate.model_validate(
                {
                    "device_id": device_id,
                    "operation_id": operation_id,
                    "size_bytes": int(length),
                    "sha256": expected_hash,
                    "media_type": request.headers.get(
                        "x-artifact-media-type", "application/octet-stream"
                    ),
                }
            ),
            device=device_id,
        )
        buffer = bytearray()
        offset = 0
        try:
            async for piece in request.stream():
                view = memoryview(piece)
                while view:
                    take = min(CHUNK_BYTES - len(buffer), len(view))
                    buffer.extend(view[:take])
                    view = view[take:]
                    if len(buffer) == CHUNK_BYTES:
                        block = bytes(buffer)
                        await self.put_bytes(
                            created["id"],
                            created["credential"],
                            offset,
                            int(length),
                            block,
                            hashlib.sha256(block).hexdigest(),
                        )
                        offset += len(block)
                        buffer.clear()
            if buffer:
                block = bytes(buffer)
                await self.put_bytes(
                    created["id"],
                    created["credential"],
                    offset,
                    int(length),
                    block,
                    hashlib.sha256(block).hexdigest(),
                )
            return await self.complete(
                created["id"],
                created["credential"],
                TransferComplete(size_bytes=int(length), sha256=expected_hash),
            )
        except BaseException:
            self.fail(created["id"], "legacy upload interrupted")
            raise
