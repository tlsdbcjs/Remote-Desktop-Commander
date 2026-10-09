"""Bounded, redacted management search across audit and diagnostic records."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from racp_domain.models import RACPError
from racp_observability.logging import safe_text, sanitize_fields
from racp_protocol.management import (
    LogItem,
    LogPage,
    LogQuery,
    ManagementPrincipal,
    SafeLogEntry,
)
from racp_protocol.models import new_id
from racp_sdk.pagination import CursorCodec
from racp_sdk.security import canonical_digest

from racp_gateway.management.authorization import authorize_management
from racp_gateway.store import GatewayStore


def _utc(value: str | None, label: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RACPError("INVALID_ARGUMENT", f"{label} is not a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise RACPError("INVALID_ARGUMENT", f"{label} must use UTC")
    return parsed.astimezone(UTC)


class LogStore:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store
        self.cursor = CursorCodec()
        self.dropped_count = 0

    def append(self, entry: SafeLogEntry) -> None:
        _utc(entry.timestamp, "timestamp")
        fields = sanitize_fields(entry.fields)
        message = safe_text(entry.message, 65536)
        self.store.db.execute(
            "INSERT INTO diagnostic_logs("
            "id,timestamp,level,event,message,device_id,request_id,actor_id,fields_json"
            ") "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                new_id("log"),
                entry.timestamp,
                entry.level,
                safe_text(entry.event, 128),
                message,
                entry.device_id or "",
                entry.request_id or "",
                entry.actor_id or "",
                json.dumps(fields, ensure_ascii=False, sort_keys=True),
            ),
        )

    def search(self, principal: ManagementPrincipal, query: LogQuery) -> LogPage:
        authorize_management(principal, "logs.read", query.device_id)
        start = _utc(query.from_utc, "from_utc")
        end = _utc(query.to_utc, "to_utc")
        if start and end and start > end:
            raise RACPError("INVALID_ARGUMENT", "from_utc must not be after to_utc")
        scope = canonical_digest(
            {
                "actor": principal.actor_id,
                "realm": principal.realm_id,
                "role": principal.role,
                "grants": sorted(principal.device_grants),
                "source": query.source,
                "device": query.device_id,
                "request": query.request_id,
                "log_actor": query.actor_id,
                "from": query.from_utc,
                "to": query.to_utc,
                "limit": query.limit,
            }
        )
        offset = (
            self.cursor.decode(query.cursor, scope, "management-logs-v1") if query.cursor else 0
        )
        clauses = ["1=1"]
        values: list[Any] = []
        if query.device_id:
            clauses.append("device_id=?")
            values.append(query.device_id)
        elif principal.role not in {"owner", "admin"}:
            if not principal.device_grants:
                return LogPage(items=[], dropped_count=self.dropped_count)
            marks = ",".join("?" for _ in principal.device_grants)
            clauses.append(f"(device_id='' OR device_id IN ({marks}))")
            values.extend(principal.device_grants)
        if query.request_id:
            clauses.append("request_id=?")
            values.append(query.request_id)
        if query.actor_id:
            clauses.append("actor_id=?")
            values.append(query.actor_id)
        if start:
            clauses.append("timestamp>=?")
            values.append(start.isoformat().replace("+00:00", "Z"))
        if end:
            clauses.append("timestamp<=?")
            values.append(end.isoformat().replace("+00:00", "Z"))
        if query.source == "audit":
            source_sql = (
                "SELECT id,'audit' AS source,timestamp,event,'INFO' AS level,'' AS message,"
                "device_id,request_id,owner_id AS actor_id,summary AS fields_json FROM audit"
            )
        elif query.source == "diagnostic":
            source_sql = (
                "SELECT id,'diagnostic' AS source,timestamp,event,level,message,"
                "device_id,request_id,actor_id,fields_json FROM diagnostic_logs"
            )
        else:
            source_sql = (
                "SELECT * FROM ("
                "SELECT id,'diagnostic' AS source,timestamp,event,level,message,"
                "device_id,request_id,actor_id,fields_json FROM diagnostic_logs UNION ALL "
                "SELECT id,'audit' AS source,timestamp,event,'INFO' AS level,'' AS message,"
                "device_id,request_id,owner_id AS actor_id,summary AS fields_json FROM audit)"
            )
        rows = self.store.db.execute(
            "SELECT * FROM ("
            + source_sql
            + ") logs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?",
            (*values, query.limit + 1, offset),
        ).fetchall()
        items = [
            LogItem(
                id=row["id"],
                source=row["source"],
                timestamp=row["timestamp"],
                event=row["event"],
                level=row["level"],
                message=row["message"],
                device_id=row["device_id"],
                request_id=row["request_id"],
                actor_id=row["actor_id"],
                fields=json.loads(row["fields_json"]),
            )
            for row in rows[: query.limit]
        ]
        next_cursor = (
            self.cursor.encode(offset + query.limit, scope, "management-logs-v1")
            if len(rows) > query.limit
            else None
        )
        return LogPage(items=items, next_cursor=next_cursor, dropped_count=self.dropped_count)

    def compact(self, *, diagnostics_days: int, max_bytes: int, now: datetime | None = None) -> int:
        if diagnostics_days < 1 or max_bytes < 1024:
            raise RACPError("INVALID_ARGUMENT", "invalid diagnostic retention policy")
        current = now or datetime.now(UTC)
        cutoff = (current - timedelta(days=diagnostics_days)).isoformat().replace("+00:00", "Z")
        deleted = 0
        with self.store.transaction():
            cursor = self.store.db.execute(
                "DELETE FROM diagnostic_logs WHERE timestamp<?", (cutoff,)
            )
            deleted += max(cursor.rowcount, 0)
            total = int(
                self.store.db.execute(
                    "SELECT COALESCE(SUM(length(message)+length(fields_json)),0) "
                    "FROM diagnostic_logs"
                ).fetchone()[0]
            )
            while total > max_bytes:
                rows = self.store.db.execute(
                    "SELECT id,length(message)+length(fields_json) AS bytes FROM diagnostic_logs "
                    "ORDER BY timestamp,id LIMIT 100"
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    self.store.db.execute("DELETE FROM diagnostic_logs WHERE id=?", (row["id"],))
                    total -= int(row["bytes"] or 0)
                    deleted += 1
                    if total <= max_bytes:
                        break
        return deleted
