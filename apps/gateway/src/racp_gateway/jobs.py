import json
import time
from typing import Any

from racp_domain.jobs import JOB_TERMINAL, OPERATION_JOB_STATE, JobState
from racp_domain.models import RACPError
from racp_protocol.jobs import JobAcceptance, JobView
from racp_protocol.models import JobUpdate, new_id, timestamp
from racp_sdk.clock import clock_identity
from racp_sdk.pagination import CursorCodec

from racp_gateway.store import GatewayStore


class JobCatalog:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store
        self.clock_id = clock_identity()
        self.cursor = CursorCodec()
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
              device_id TEXT NOT NULL, owner_id TEXT NOT NULL, state TEXT NOT NULL,
              revision INTEGER NOT NULL, progress REAL, waiting_reason TEXT,
              progress_revision INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operation_admission (
              operation_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              state TEXT NOT NULL, deadline REAL NOT NULL, clock_id TEXT NOT NULL,
              enqueued_at TEXT NOT NULL, cancel_sent REAL);
            CREATE INDEX IF NOT EXISTS admission_device_state
              ON operation_admission(device_id,state);
            CREATE INDEX IF NOT EXISTS jobs_owner_created ON jobs(owner_id,created_at,id);
        """)
        columns = {row[1] for row in store.db.execute("PRAGMA table_info(operation_admission)")}
        if "cancel_reason" not in columns:
            store.db.execute("ALTER TABLE operation_admission ADD COLUMN cancel_reason TEXT")
        store.db.execute(
            "UPDATE operation_admission SET cancel_reason='requested' WHERE cancel_reason IS NULL "
            "AND operation_id IN (SELECT id FROM operations WHERE state='CANCEL_REQUESTED')"
        )
        # Upgrade earlier development records without changing execution facts.
        for record in store.iter_records():
            request = record["request"]
            if (
                request.get("execution_mode") == "job"
                and record["state"] != "ACCEPTED"
                and not self.by_operation(record["id"])
            ):
                job_id = request.get("job_id") or new_id("job")
                request["job_id"] = job_id
                with store.transaction():
                    store.db.execute(
                        "UPDATE operations SET request=? WHERE id=?",
                        (json.dumps(request), record["id"]),
                    )
                    store.db.execute(
                        "INSERT INTO jobs VALUES (?,?,?,?,?,1,NULL,NULL,0,?,?)",
                        (
                            job_id,
                            record["id"],
                            record["device_id"],
                            request["context"]["principal_id"],
                            OPERATION_JOB_STATE[record["state"]],
                            record["created_at"],
                            record["updated_at"],
                        ),
                    )

    def by_operation(self, operation_id: str) -> dict[str, Any] | None:
        row = self.store.db.execute(
            "SELECT * FROM jobs WHERE operation_id=?", (operation_id,)
        ).fetchone()
        return dict(row) if row else None

    def enqueue(
        self, record: dict[str, Any], *, approval_id: str | None, payload_hash: str
    ) -> dict[str, Any]:
        operation_id, request = record["id"], record["request"]
        existing = self.store.db.execute(
            "SELECT * FROM operation_admission WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if existing:
            return dict(existing)
        with self.store.transaction():
            queued = self.store.db.execute(
                "SELECT COUNT(*) FROM operation_admission WHERE device_id=? AND state='QUEUED'",
                (record["device_id"],),
            ).fetchone()[0]
            if queued >= 64:
                raise RACPError(
                    "RESOURCE_EXHAUSTED",
                    "device waiting queue full",
                    retry_after_ms=1000,
                    operation_id=operation_id,
                )
            if approval_id is not None:
                self.store.consume_approval(approval_id, operation_id, payload_hash)
            if request["execution_mode"] == "job" and self.by_operation(operation_id) is None:
                job_id = new_id("job")
                request["job_id"] = job_id
                self.store.db.execute(
                    "UPDATE operations SET request=? WHERE id=?",
                    (json.dumps(request), operation_id),
                )
                now = timestamp()
                self.store.db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,?,1,NULL,NULL,0,?,?)",
                    (
                        job_id,
                        operation_id,
                        record["device_id"],
                        request["context"]["principal_id"],
                        "QUEUED",
                        now,
                        now,
                    ),
                )
            self.store.db.execute(
                "INSERT INTO operation_admission (operation_id,device_id,owner_id,state,"
                "deadline,clock_id,enqueued_at,cancel_sent) "
                "VALUES (?,?,?,'QUEUED',?,?,?,NULL)",
                (
                    operation_id,
                    record["device_id"],
                    request["context"]["principal_id"],
                    time.monotonic() + request["timeout_ms"] / 1000,
                    self.clock_id,
                    timestamp(),
                ),
            )
            self.store.audit("enqueued", request, timeout_ms=request["timeout_ms"])
        return dict(
            self.store.db.execute(
                "SELECT * FROM operation_admission WHERE operation_id=?", (operation_id,)
            ).fetchone()
        )

    def get(self, job_id: str, owner: str, *, include_expired: bool = False) -> dict[str, Any]:
        row = self.store.db.execute(
            "SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner)
        ).fetchone()
        if row is None:
            raise RACPError("JOB_NOT_FOUND", "job was not found")
        value = dict(row)
        self.store.device(value["device_id"], owner)
        operation = self.store.get(value["operation_id"])
        if not include_expired:
            self.store.require_outcome(operation)
        return JobView.model_validate(
            {
                **value,
                "state": JobState(value["state"]),
                "job_id": value["id"],
                "operation": operation["request"]["operation"],
                "timeout_ms": operation["request"]["timeout_ms"],
                "result": operation["result"],
                "error": operation["error"],
                "outcome_available": bool(operation["outcome_available"]),
                "poll_after_ms": None if value["state"] in JOB_TERMINAL else 250,
            }
        ).model_dump(mode="json")

    def acceptance(self, operation_id: str, owner: str) -> dict[str, Any]:
        job = self.by_operation(operation_id)
        if job is None:
            raise RACPError("JOB_NOT_FOUND", "job was not admitted")
        if job["owner_id"] != owner:
            raise RACPError("PERMISSION_DENIED", "job belongs to another principal")
        self.store.require_outcome(self.store.get(operation_id))
        return JobAcceptance.model_validate(
            {
                "operation_id": operation_id,
                "job_id": job["id"],
                "device_id": job["device_id"],
                "state": JobState(job["state"]),
                "poll_after_ms": None if job["state"] in JOB_TERMINAL else 250,
            }
        ).model_dump(mode="json")

    def list(
        self,
        owner: str,
        *,
        device: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        if device:
            self.store.device(device, owner)
        query = "jobs:" + owner + ":" + str(device) + ":" + str(limit) + ":" + str(state)
        before = self.cursor.decode(cursor, query, "keyset-v1") if cursor else 2**63 - 1
        rows = self.store.db.execute(
            "SELECT rowid AS position,id FROM jobs WHERE owner_id=? AND (? IS NULL OR device_id=?) "
            "AND (? IS NULL OR state=?) AND rowid<? ORDER BY rowid DESC LIMIT ?",
            (owner, device, device, state, state, before, limit + 1),
        ).fetchall()
        return {
            "items": [self.get(row["id"], owner, include_expired=True) for row in rows[:limit]],
            "next_cursor": self.cursor.encode(rows[limit - 1]["position"], query, "keyset-v1")
            if len(rows) > limit
            else None,
            "consistency": "best_effort",
            "sort": "rowid_desc",
        }

    def update(self, message: JobUpdate, *, reconcile: bool = False) -> None:
        operation = self.store.get(message.operation_id)
        job = self.by_operation(message.operation_id)
        if job is None or operation["state"] in {
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "TIMED_OUT",
            "UNKNOWN",
        }:
            return
        if message.revision < job["progress_revision"] or (
            message.revision == job["progress_revision"] and not reconcile
        ):
            return
        if job["state"] == "CANCEL_REQUESTED":
            return
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE jobs SET state=?,waiting_reason=?,progress=?,progress_revision=?,"
                "revision=revision+1,updated_at=? WHERE id=?",
                (
                    message.state,
                    message.waiting_reason,
                    message.progress,
                    message.revision,
                    timestamp(),
                    job["id"],
                ),
            )
            self.store.audit(
                "job_state_changed",
                operation["request"],
                job_id=job["id"],
                state=message.state,
                waiting_reason=message.waiting_reason,
                progress=message.progress,
            )
