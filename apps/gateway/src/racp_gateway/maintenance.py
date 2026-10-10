"""Maintenance admission and drain coordination for backup/update operations."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time

from racp_domain.models import TERMINAL_STATES, RACPError
from racp_protocol.management import (
    DrainReport,
    MaintenanceJobView,
    ManagementPrincipal,
)
from racp_protocol.models import new_id, timestamp

from racp_gateway.management.authorization import authorize_management
from racp_gateway.service import ControlPlane
from racp_gateway.store import GatewayStore


class MaintenanceCoordinator:
    def __init__(self, store: GatewayStore, control: ControlPlane) -> None:
        self.store = store
        self.control = control

    def begin(
        self,
        principal: ManagementPrincipal,
        kind: str,
        key: str,
    ) -> MaintenanceJobView:
        if kind not in {"backup", "restore", "update"}:
            raise ValueError("unsupported maintenance kind")
        if kind == "update":
            authorize_management(principal, "updates.write")
        else:
            authorize_management(principal, "backups.write")
        row = self.store.db.execute(
            "SELECT * FROM maintenance_jobs WHERE realm_id=? AND kind=? AND idempotency_key=?",
            (principal.realm_id, kind, key),
        ).fetchone()
        if row is None:
            now = timestamp()
            job_id = new_id("mnt")
            with self.store.transaction():
                self.store.db.execute(
                    "INSERT INTO maintenance_jobs("
                    "id,kind,state,actor_id,realm_id,idempotency_key,created_at,updated_at,progress"
                    ") VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        kind,
                        "PENDING",
                        principal.actor_id,
                        principal.realm_id,
                        key,
                        now,
                        now,
                        0.0,
                    ),
                )
            row = self.store.db.execute(
                "SELECT * FROM maintenance_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        assert row is not None
        return self._view(row)

    def _view(self, row: sqlite3.Row) -> MaintenanceJobView:
        value = dict(row)
        return MaintenanceJobView(
            id=value["id"],
            kind=value["kind"],
            state=value["state"],
            actor_id=value["actor_id"],
            realm_id=value["realm_id"],
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            progress=value["progress"],
            error=value["error"],
            receipt_id=value["receipt_id"],
        )

    def get(self, principal: ManagementPrincipal, job_id: str) -> MaintenanceJobView:
        row = self.store.db.execute(
            "SELECT * FROM maintenance_jobs WHERE id=? AND realm_id=?",
            (job_id, principal.realm_id),
        ).fetchone()
        if row is None:
            raise RACPError("NOT_FOUND", "maintenance job was not found")
        kind = str(row["kind"])
        authorize_management(
            principal,
            "updates.read" if kind == "update" else "backups.read",
        )
        return self._view(row)

    def set_state(
        self,
        job_id: str,
        state: str,
        *,
        progress: float | None = None,
        error: str | None = None,
        receipt: dict[str, object] | None = None,
    ) -> MaintenanceJobView:
        receipt_id = None
        with self.store.transaction():
            if receipt is not None:
                receipt_id = new_id("mrc")
                kind = str(
                    self.store.db.execute(
                        "SELECT kind FROM maintenance_jobs WHERE id=?",
                        (job_id,),
                    ).fetchone()[0]
                )
                self.store.db.execute(
                    "INSERT INTO maintenance_receipts(id,job_id,kind,state,payload,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        receipt_id,
                        job_id,
                        kind,
                        state,
                        json.dumps(receipt, sort_keys=True),
                        timestamp(),
                    ),
                )
            self.store.db.execute(
                "UPDATE maintenance_jobs SET state=?,updated_at=?,progress=COALESCE(?,progress),"
                "error=?,receipt_id=COALESCE(?,receipt_id) WHERE id=?",
                (state, timestamp(), progress, error, receipt_id, job_id),
            )
        row = self.store.db.execute(
            "SELECT * FROM maintenance_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._view(row)

    def blockers(self) -> tuple[list[str], list[str]]:
        operations = [
            str(row["id"])
            for row in self.store.db.execute(
                "SELECT id,state FROM operations ORDER BY created_at"
            ).fetchall()
            if row["state"] not in TERMINAL_STATES
        ]
        handles = [
            str(row["id"])
            for row in self.store.db.execute(
                "SELECT id FROM handles "
                "WHERE json_extract(record,'$.state') IN ('ACTIVE','CREATING','CLOSING') "
                "ORDER BY observed_at"
            ).fetchall()
        ]
        return operations, handles

    async def drain(self, timeout_seconds: int = 120) -> DrainReport:
        deadline = time.monotonic() + timeout_seconds
        self.control.maintenance_reason = "maintenance drain"
        while True:
            operations, handles = self.blockers()
            if not operations and not handles:
                return DrainReport(
                    state="READY",
                    active_operations=[],
                    active_handles=[],
                    timeout_seconds=timeout_seconds,
                )
            if time.monotonic() >= deadline:
                return DrainReport(
                    state="DEFERRED",
                    active_operations=operations,
                    active_handles=handles,
                    timeout_seconds=timeout_seconds,
                )
            await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def end(self) -> None:
        self.control.maintenance_reason = None
