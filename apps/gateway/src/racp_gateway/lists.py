"""Owner-scoped keyset snapshots. Cursors bind filters and fixed descending order."""

import json
import time
from collections.abc import Callable
from typing import Any

from racp_sdk.pagination import CursorCodec
from racp_sdk.security import canonical_digest

from racp_gateway.store import GatewayStore


class ConsoleLists:
    def __init__(self, store: GatewayStore) -> None:
        self.store, self.cursor = store, CursorCodec()
        store.db.executescript("""
          CREATE INDEX IF NOT EXISTS audit_owner ON audit(owner_id,device_id,event);
          CREATE INDEX IF NOT EXISTS devices_owner ON devices(owner_id);
        """)

    def page(
        self,
        kind: str,
        owner: str,
        *,
        limit: int,
        cursor: str | None,
        state: str | None = None,
        device_id: str | None = None,
        convert: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if device_id:
            self.store.device(device_id, owner)
        scope = canonical_digest(
            {
                "kind": kind,
                "owner": owner,
                "state": state,
                "device": device_id,
                "limit": limit,
                "sort": "rowid_desc",
            }
        )
        before = self.cursor.decode(cursor, scope, "keyset-v1") if cursor else 2**63 - 1
        clauses, values = ["t.owner_id=?", "t.rowid<?"], [owner, before]
        # All SQL identifiers are selected from this fixed internal registry.
        table = {
            "devices": "devices",
            "artifacts": "artifacts",
            "audit": "audit",
            "approvals": "approvals",
        }[kind]
        if kind == "approvals":
            # Approval expiry projection must precede state filtering.
            self.store.db.execute(
                "UPDATE approvals SET state='EXPIRED' WHERE state IN ('PENDING','APPROVED') "
                "AND expires<=?",
                (time.time(),),
            )
            sql = (
                "SELECT t.rowid AS _position,t.* FROM approvals t "
                "JOIN operations o ON o.id=t.operation_id JOIN devices d ON d.id=o.device_id "
            )
            clauses[0] = "d.owner_id=?"
            if device_id:
                clauses.append("o.device_id=?")
                values.append(device_id)
        else:
            sql = f"SELECT t.rowid AS _position,t.* FROM {table} t "
            if device_id and kind != "devices":
                clauses.append("t.device_id=?")
                values.append(device_id)
        if state:
            clauses.append(
                "COALESCE(json_extract(t.info,'$.status'),'OFFLINE')=?"
                if kind == "devices"
                else "t.event=?"
                if kind == "audit"
                else "t.state=?"
            )
            values.append(state)
        rows = self.store.db.execute(
            sql + "WHERE " + " AND ".join(clauses) + " ORDER BY t.rowid DESC LIMIT ?",
            (*values, limit + 1),
        ).fetchall()
        items = []
        for row in rows[:limit]:
            item = dict(row)
            item.pop("_position")
            if kind == "audit":
                item["summary"] = json.loads(item["summary"])
            if kind == "devices":
                item["info"] = json.loads(item["info"])
            items.append(convert(item) if convert else item)
        return {
            "items": items,
            "next_cursor": self.cursor.encode(rows[limit - 1]["_position"], scope, "keyset-v1")
            if len(rows) > limit
            else None,
            "consistency": "best_effort",
            "sort": "rowid_desc",
        }
