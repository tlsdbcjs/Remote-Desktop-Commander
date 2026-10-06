from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from racp_domain.models import RACPError
from racp_protocol.artifacts import ARTIFACT_INPUT_OPERATIONS
from racp_protocol.models import Cancel, Request, new_id

if TYPE_CHECKING:
    from racp_gateway.service import Connection, ControlPlane


class Scheduler:
    def __init__(self, control: ControlPlane) -> None:
        self.control = control
        self.wake = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.stopping = False
        self.cancel_epochs: dict[str, int] = {}

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    def remaining(self, admission: dict[str, Any]) -> int:
        if admission["clock_id"] != self.control.jobs.clock_id:
            return 0
        return max(0, math.floor((float(admission["deadline"]) - time.monotonic()) * 1000))

    async def cancel_deadline(self, admission: dict[str, Any], record: dict[str, Any]) -> None:
        connection = self.control.connections.get(record["device_id"])
        first_sent = admission["cancel_sent"]
        if first_sent is not None and (
            admission["clock_id"] != self.control.jobs.clock_id
            or time.monotonic() - first_sent >= 5
        ):
            self.finish(
                record,
                "UNKNOWN",
                RACPError(
                    "EXECUTION_UNKNOWN",
                    "termination/result confirmation exceeded cleanup grace or clock continuity",
                    execution_state="unknown",
                    cleanup_status="unknown",
                ),
            )
            return
        if not connection or not connection.ready:
            if first_sent is None and self.remaining(admission) == 0:
                self.finish(
                    record,
                    "UNKNOWN",
                    RACPError(
                        "EXECUTION_UNKNOWN",
                        "deadline elapsed while Device is offline",
                        execution_state="unknown",
                        cleanup_status="unknown",
                    ),
                )
            return
        if self.cancel_epochs.get(record["id"]) == connection.epoch:
            return
        self.control.store.transition(record["id"], "CANCEL_REQUESTED")
        if first_sent is None:
            self.control.store.db.execute(
                "UPDATE operation_admission SET cancel_sent=?,"
                "cancel_reason=COALESCE(cancel_reason,'deadline') WHERE operation_id=?",
                (time.monotonic(), record["id"]),
            )
        self.cancel_epochs[record["id"]] = connection.epoch
        message = Cancel(
            device_id=record["device_id"],
            agent_boot_id=connection.boot_id,
            connection_epoch=connection.epoch,
            request_id=new_id("req"),
            target_operation_id=record["id"],
            reason=admission.get("cancel_reason") or "deadline",
        )

        async def deliver_cancel() -> None:
            try:
                await connection.send(message)
            except (OSError, RACPError):
                latest = self.control.store.get(record["id"])
                if latest["state"] not in {
                    "SUCCEEDED",
                    "FAILED",
                    "CANCELLED",
                    "TIMED_OUT",
                    "UNKNOWN",
                }:
                    self.control.store.transition(record["id"], "RECONCILING")

        task = asyncio.create_task(deliver_cancel())
        self.control.pending_tasks.add(task)
        task.add_done_callback(self.control.pending_tasks.discard)

    def finish(self, record: dict[str, Any], state: str, error: RACPError) -> None:
        self.control.store.transition(record["id"], state, error=asdict(error.error))
        self.cancel_epochs.pop(record["id"], None)
        self.control.events.setdefault(record["id"], asyncio.Event()).set()
        self.control.events.pop(record["id"], None)
        self.control.locks.pop(record["id"], None)
        self.wake.set()

    async def tick(self) -> None:
        store = self.control.store
        rows = store.db.execute(
            "SELECT * FROM operation_admission WHERE state!='DONE' ORDER BY rowid"
        ).fetchall()
        for row in rows:
            admission = dict(row)
            record = store.get(admission["operation_id"])
            remaining = self.remaining(admission)
            if admission.get("cancel_reason") is not None and admission["state"] == "SENT":
                connection = self.control.connections.get(record["device_id"])
                if (
                    connection
                    and connection.ready
                    or remaining == 0
                    or admission["cancel_sent"] is not None
                ):
                    await self.cancel_deadline(admission, record)
                continue
            if remaining == 0:
                if admission["state"] == "QUEUED":
                    self.finish(
                        record,
                        "TIMED_OUT",
                        RACPError(
                            "TIMEOUT",
                            "execution budget elapsed in queue"
                            if admission["clock_id"] == self.control.jobs.clock_id
                            else "clock epoch changed; queued budget invalidated",
                            layer="gateway",
                        ),
                    )
                else:
                    await self.cancel_deadline(admission, record)
                continue
            if admission["state"] != "QUEUED":
                continue
            connection = self.control.connections.get(record["device_id"])
            if not connection or not connection.ready:
                continue
            enrolled = store.device(record["device_id"])
            if enrolled["revoked"]:
                self.finish(
                    record, "CANCELLED", RACPError("DEVICE_REVOKED", "queued operation revoked")
                )
                continue
            active = store.db.execute(
                "SELECT COUNT(*) FROM operation_admission WHERE device_id=? AND state='SENT'",
                (record["device_id"],),
            ).fetchone()[0]
            if active >= 16:
                continue
            request = Request.model_validate(record["request"])
            if request.operation not in connection.capabilities:
                self.finish(
                    record,
                    "FAILED",
                    RACPError("CAPABILITY_UNAVAILABLE", "queued capability became unavailable"),
                )
                continue
            if request.operation in ARTIFACT_INPUT_OPERATIONS and request.payload.get(
                "artifact_id"
            ):
                try:
                    assert self.control.artifacts is not None
                    self.control.artifacts.ready(
                        request.payload["artifact_id"], request.context.principal_id
                    )
                except RACPError as exc:
                    self.finish(record, "FAILED", exc)
                    continue
            outgoing = request.model_copy(
                update={
                    "agent_boot_id": connection.boot_id,
                    "connection_epoch": connection.epoch,
                    "remaining_timeout_ms": remaining,
                }
            )
            # Both dispatch intent and projected Job state commit before transport I/O.
            with store.transaction():
                store.db.execute(
                    "UPDATE operation_admission SET state='SENT' WHERE operation_id=?",
                    (record["id"],),
                )
                store.db.execute(
                    "UPDATE operations SET state='DISPATCHED' WHERE id=?", (record["id"],)
                )
                store.on_transition(record, "DISPATCHED")
                store.audit("dispatch", outgoing.model_dump(), remaining_timeout_ms=remaining)

            async def deliver(
                request: Request, operation_id: str, deadline: float, channel: Connection
            ) -> None:
                try:
                    await channel.send(request, deadline=deadline)
                except RACPError as exc:
                    latest = store.get(operation_id)
                    if exc.error.code == "TIMEOUT" and exc.error.execution_state == "not_started":
                        self.finish(latest, "TIMED_OUT", exc)
                    elif latest["state"] not in {
                        "SUCCEEDED",
                        "FAILED",
                        "CANCELLED",
                        "TIMED_OUT",
                        "UNKNOWN",
                    }:
                        store.transition(operation_id, "RECONCILING")
                except OSError:
                    latest = store.get(operation_id)
                    if latest["state"] not in {
                        "SUCCEEDED",
                        "FAILED",
                        "CANCELLED",
                        "TIMED_OUT",
                        "UNKNOWN",
                    }:
                        store.transition(operation_id, "RECONCILING")

            task = asyncio.create_task(
                deliver(outgoing, record["id"], admission["deadline"], connection)
            )
            self.control.pending_tasks.add(task)
            task.add_done_callback(self.control.pending_tasks.discard)

    async def run(self) -> None:
        while not self.stopping:
            self.wake.clear()
            await self.tick()
            try:
                await asyncio.wait_for(self.wake.wait(), 0.05)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self.stopping = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for task in list(self.control.pending_tasks):
            task.cancel()
        await asyncio.gather(*list(self.control.pending_tasks), return_exceptions=True)
