import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from racp_domain.models import TERMINAL_STATES, RACPError
from racp_protocol.models import new_id, timestamp
from racp_protocol.registry import REGISTRY

from racp_sdk.clock import clock_identity


class Journal:
    """Single-event-loop SQLite adapter. No await occurs inside a transaction."""

    def __init__(self, path: Path) -> None:
        self.retention_clock_id = clock_identity()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1}:
            raise RuntimeError("unsupported database schema version")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS outcome_retention_clocks (
              id TEXT PRIMARY KEY, clock_id TEXT NOT NULL, committed_monotonic REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS operations (
              id TEXT PRIMARY KEY, scope TEXT NOT NULL, key_hash TEXT NOT NULL,
              payload_hash TEXT NOT NULL, device_id TEXT NOT NULL, request TEXT NOT NULL,
              state TEXT NOT NULL, result TEXT, error TEXT, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, UNIQUE(scope, key_hash));
            CREATE TABLE IF NOT EXISTS audit (
              id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, event TEXT NOT NULL,
              device_id TEXT NOT NULL, operation_id TEXT NOT NULL,
              operation TEXT NOT NULL, trace_id TEXT NOT NULL, request_id TEXT NOT NULL,
              summary TEXT NOT NULL);
            PRAGMA user_version=1;
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(operations)")}
        audit_columns = {row[1] for row in self.db.execute("PRAGMA table_info(audit)")}
        if "owner_id" not in audit_columns:
            self.db.execute(
                "ALTER TABLE audit ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'owner_local'"
            )
        for name, definition in (
            ("outcome_available", "INTEGER NOT NULL DEFAULT 1"),
            ("mutation", "INTEGER NOT NULL DEFAULT 1"),
            ("retain_key", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if name not in columns:
                self.db.execute(f"ALTER TABLE operations ADD COLUMN {name} {definition}")
        if "mutation" not in columns:
            for name, spec in REGISTRY.items():
                if not spec.side_effect:
                    self.db.execute(
                        "UPDATE operations SET mutation=0 "
                        "WHERE json_extract(request,'$.operation')=?",
                        (name,),
                    )
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS idempotency_tombstones (
              scope TEXT NOT NULL, key_hash TEXT NOT NULL, payload_hash TEXT NOT NULL,
              operation_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL,
              outcome_available INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(scope,key_hash));
            CREATE TABLE IF NOT EXISTS operation_resolutions (
              id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, digest TEXT NOT NULL,
              state TEXT NOT NULL, result TEXT, error TEXT, observed_at TEXT NOT NULL,
              outcome_available INTEGER NOT NULL DEFAULT 1,
              UNIQUE(operation_id,digest));
            CREATE INDEX IF NOT EXISTS operations_retention
              ON operations(outcome_available,state,updated_at);
            CREATE INDEX IF NOT EXISTS operations_mutation_device ON operations(device_id,mutation);
            CREATE INDEX IF NOT EXISTS retired_transient_outcomes
              ON audit(operation_id,device_id,request_id,trace_id)
              WHERE event='transient_outcome_retired';
        """)
        self.tombstone_limit = 1000000

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.db.close()

    def audit(self, event: str, request: dict[str, Any], **summary: Any) -> None:
        self.db.execute(
            "INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                new_id("aud"),
                timestamp(),
                event,
                request.get("device_id", ""),
                request.get("operation_id", ""),
                request.get("operation", ""),
                request.get("trace_id", ""),
                request.get("request_id", ""),
                json.dumps(summary, sort_keys=True),
                request.get("context", {}).get("principal_id", "owner_local"),
            ),
        )

    def accept(
        self,
        scope: str,
        key_hash: str,
        payload_hash: str,
        request: dict[str, Any],
        *,
        retain_key: bool | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM operations WHERE scope=? AND key_hash=?", (scope, key_hash)
            ).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    raise RACPError("IDEMPOTENCY_CONFLICT", "key already binds another payload")
                if not row["outcome_available"]:
                    raise RACPError(
                        "OPERATION_EXPIRED",
                        "stored outcome expired; this key cannot run again",
                        operation_id=row["id"],
                        outcome_available=False,
                    )
                return self._decode(row), False
            mutation = REGISTRY.get(request.get("operation", ""))
            is_mutation = mutation is None or mutation.side_effect
            if is_mutation:
                count = self.db.execute(
                    "SELECT COUNT(*) FROM operations WHERE device_id=? AND mutation=1",
                    (request["device_id"],),
                ).fetchone()[0]
                if count >= self.tombstone_limit:
                    raise RACPError(
                        "RESOURCE_EXHAUSTED",
                        "device mutation/tombstone capacity reached",
                        next_action="export_and_register_new_device",
                    )
            if retain_key is None:
                retain_key = bool(request.get("retain_key", True))
            now = timestamp()
            self.db.execute(
                "INSERT INTO operations (id,scope,key_hash,payload_hash,device_id,request,"
                "state,result,error,created_at,updated_at,outcome_available,mutation,retain_key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (
                    request["operation_id"],
                    scope,
                    key_hash,
                    payload_hash,
                    request["device_id"],
                    json.dumps(request),
                    "ACCEPTED",
                    None,
                    None,
                    now,
                    now,
                    int(is_mutation),
                    int(retain_key or is_mutation),
                ),
            )
            self.audit("accepted", request, payload_hash=payload_hash)
        return self.get(request["operation_id"]), True

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for key in ("request", "result", "error"):
            record[key] = json.loads(record[key]) if record[key] else None
        return record

    def get(self, operation_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise RACPError("OPERATION_NOT_FOUND", "operation was not found")
        return self._decode(row)

    def retired_transient_outcome(
        self, operation_id: str, device_id: str, request_id: str, trace_id: str
    ) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM audit WHERE event='transient_outcome_retired' AND operation_id=? "
                "AND device_id=? AND request_id=? AND trace_id=? LIMIT 1",
                (operation_id, device_id, request_id, trace_id),
            ).fetchone()
            is not None
        )

    def transition(
        self,
        operation_id: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.transaction():
            previous = self.get(operation_id)
            if previous["state"] in TERMINAL_STATES:
                if (
                    previous["state"] == "UNKNOWN"
                    and state in TERMINAL_STATES
                    and state != "UNKNOWN"
                ):
                    self.record_resolution(previous, state, result, error)
                return previous
            self.db.execute(
                "UPDATE operations SET state=?,result=?,error=?,updated_at=? WHERE id=?",
                (
                    state,
                    json.dumps(result) if result is not None else None,
                    json.dumps(error) if error is not None else None,
                    timestamp(),
                    operation_id,
                ),
            )
            self.on_transition(previous, state)
            if state in TERMINAL_STATES:
                self.db.execute(
                    "INSERT OR REPLACE INTO outcome_retention_clocks VALUES (?,?,?)",
                    (operation_id, self.retention_clock_id, monotonic()),
                )
            self.audit("state_changed", previous["request"], state=state)
        return self.get(operation_id)

    def on_transition(self, previous: dict[str, Any], state: str) -> None:
        """Subclasses update projections inside the same durable transaction."""

    def require_outcome(self, record: dict[str, Any]) -> None:
        if not record["outcome_available"]:
            raise RACPError(
                "OPERATION_EXPIRED",
                "stored outcome expired; this key cannot run again",
                operation_id=record["id"],
                outcome_available=False,
            )

    def record_resolution(
        self,
        previous: dict[str, Any],
        state: str,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
    ) -> None:
        content = json.dumps(
            {"state": state, "result": result, "error": error}, sort_keys=True, ensure_ascii=False
        )
        digest = hashlib.sha256(content.encode()).hexdigest()
        row = self.db.execute(
            "SELECT 1 FROM operation_resolutions WHERE operation_id=? AND digest=?",
            (previous["id"], digest),
        ).fetchone()
        if row:
            return
        count = self.db.execute(
            "SELECT COUNT(*) FROM operation_resolutions WHERE operation_id=?", (previous["id"],)
        ).fetchone()[0]
        if count >= 16:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "resolution record limit reached", operation_id=previous["id"]
            )
        resolution_id = new_id("resolution")
        self.db.execute(
            "INSERT INTO operation_resolutions VALUES (?,?,?,?,?,?,?,1)",
            (
                resolution_id,
                previous["id"],
                digest,
                state,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                json.dumps(error, ensure_ascii=False) if error is not None else None,
                timestamp(),
            ),
        )
        self.audit(
            "late_result_recorded",
            previous["request"],
            resolution_id=resolution_id,
            reported_state=state,
        )
        self.db.execute(
            "INSERT INTO outcome_retention_clocks VALUES (?,?,?)",
            (resolution_id, self.retention_clock_id, monotonic()),
        )

    def retention_elapsed(self, id: str, seconds: int) -> bool:
        row = self.db.execute("SELECT * FROM outcome_retention_clocks WHERE id=?", (id,)).fetchone()
        current = monotonic()
        if (
            row is None
            or row["clock_id"] != self.retention_clock_id
            or current < row["committed_monotonic"]
        ):
            # Older schemas and OS restarts lack trustworthy elapsed time. Extend
            # retention by a full interval instead of trusting a forward UTC jump.
            self.db.execute(
                "INSERT OR REPLACE INTO outcome_retention_clocks VALUES (?,?,?)",
                (id, self.retention_clock_id, current),
            )
            return False
        return bool(current - row["committed_monotonic"] >= seconds)

    def resolutions(self, operation_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM operation_resolutions WHERE operation_id=? ORDER BY observed_at,id",
            (operation_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "result": json.loads(row["result"]) if row["result"] else None,
                "error": json.loads(row["error"]) if row["error"] else None,
                "outcome_available": bool(row["outcome_available"]),
            }
            for row in rows
        ]

    def compact(
        self,
        *,
        now: datetime | None = None,
        retention_seconds: int = 86400,
        pinned_operations: set[str] | None = None,
    ) -> dict[str, int]:
        if retention_seconds < 86400:
            raise RACPError("INVALID_ARGUMENT", "outcome retention must be at least 24 hours")
        current = now or datetime.now(UTC)
        cutoff = (current - timedelta(seconds=retention_seconds)).isoformat().replace("+00:00", "Z")
        states = tuple(str(value) for value in TERMINAL_STATES)
        marks = ",".join("?" for _ in states)
        excluded = sorted(pinned_operations or ())
        exclusion = ""
        if excluded:
            exclusion = " AND id NOT IN (" + ",".join("?" for _ in excluded) + ")"
        rows = self.db.execute(
            f"SELECT * FROM operations WHERE outcome_available=1 AND state IN ({marks}) "
            "AND updated_at<=?" + exclusion + " ORDER BY rowid LIMIT 1000",
            (*states, cutoff, *excluded),
        ).fetchall()
        expired, removed = 0, 0
        with self.transaction():
            for row in rows:
                if pinned_operations and row["id"] in pinned_operations:
                    continue
                if now is None and not self.retention_elapsed(row["id"], retention_seconds):
                    continue
                request = json.loads(row["request"])
                if not row["retain_key"] and not row["mutation"]:
                    self.audit("transient_outcome_retired", request, state=row["state"])
                    self.db.execute("DELETE FROM operations WHERE id=?", (row["id"],))
                    self.db.execute("DELETE FROM outcome_retention_clocks WHERE id=?", (row["id"],))
                    removed += 1
                    continue
                if row["mutation"]:
                    self.db.execute(
                        "INSERT OR IGNORE INTO idempotency_tombstones VALUES (?,?,?,?,?,0)",
                        (
                            row["scope"],
                            row["key_hash"],
                            row["payload_hash"],
                            row["id"],
                            row["device_id"],
                        ),
                    )
                metadata = {
                    key: value
                    for key, value in request.items()
                    if key not in {"payload", "idempotency_key"}
                }
                self.db.execute(
                    "UPDATE operations SET request=?,result=NULL,error=NULL,outcome_available=0 "
                    "WHERE id=?",
                    (json.dumps(metadata), row["id"]),
                )
                self.audit("outcome_expired", metadata, mutation=bool(row["mutation"]))
                self.db.execute("DELETE FROM outcome_retention_clocks WHERE id=?", (row["id"],))
                expired += 1
            for observation in self.db.execute(
                "SELECT id FROM operation_resolutions WHERE outcome_available=1 "
                "AND observed_at<=? LIMIT 1000",
                (cutoff,),
            ).fetchall():
                if now is None and not self.retention_elapsed(observation["id"], retention_seconds):
                    continue
                self.db.execute(
                    "UPDATE operation_resolutions SET result=NULL,error=NULL,outcome_available=0 "
                    "WHERE id=?",
                    (observation["id"],),
                )
                self.db.execute(
                    "DELETE FROM outcome_retention_clocks WHERE id=?", (observation["id"],)
                )
            audit_cutoff = (current - timedelta(days=30)).isoformat().replace("+00:00", "Z")
            self.db.execute(
                "DELETE FROM audit WHERE id IN (SELECT id FROM audit WHERE timestamp<? LIMIT 1000)",
                (audit_cutoff,),
            )
        return {"outcomes_expired": expired, "ephemeral_reads_removed": removed}

    def records(self, device_id: str | None = None) -> list[dict[str, Any]]:
        if device_id:
            rows = self.db.execute(
                "SELECT * FROM operations WHERE device_id=? ORDER BY created_at", (device_id,)
            ).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM operations ORDER BY created_at").fetchall()
        return [self._decode(row) for row in rows]

    def iter_records(
        self, device_id: str | None = None, *, nonterminal: bool = False
    ) -> Iterator[dict[str, Any]]:
        clauses = ["outcome_available=1"]
        values: list[Any] = []
        if device_id:
            clauses.append("device_id=?")
            values.append(device_id)
        if nonterminal:
            clauses.append("state NOT IN ('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','UNKNOWN')")
        cursor = self.db.execute(
            "SELECT * FROM operations WHERE " + " AND ".join(clauses) + " ORDER BY rowid", values
        )
        while rows := cursor.fetchmany(100):
            for row in rows:
                yield self._decode(row)

    def recover_agent(self) -> None:
        for record in self.iter_records(nonterminal=True):
            if record["state"] not in TERMINAL_STATES:
                error = RACPError(
                    "EXECUTION_UNKNOWN",
                    "agent restarted before durable result",
                    layer="agent",
                    execution_state="unknown",
                )
                self.transition(record["id"], "UNKNOWN", error=asdict(error.error))
