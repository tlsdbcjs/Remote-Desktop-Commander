import asyncio
import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from racp_domain.models import TERMINAL_STATES, AgentChannel, ArtifactCatalog, RACPError
from racp_observability.logging import log_event
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.artifacts import ARTIFACT_INPUT_OPERATIONS
from racp_protocol.models import (
    Cancel,
    Context,
    ExecutionSnapshot,
    JobUpdate,
    OperationInput,
    OutcomeExpired,
    Request,
    Result,
    new_id,
    timestamp,
)
from racp_protocol.registry import REGISTRY, validate_payload
from racp_protocol.resolutions import OperationResolutions
from racp_sdk.peer_writer import PeerWriter
from racp_sdk.security import canonical_digest, digest

from racp_gateway.browser_events import BrowserEvents
from racp_gateway.handles import HandleCatalog
from racp_gateway.jobs import JobCatalog
from racp_gateway.scheduler import Scheduler
from racp_gateway.store import GatewayStore
from racp_gateway.terminal_streams import StreamBroker


@dataclass
class Connection:
    socket: AgentChannel
    boot_id: str
    epoch: int
    capabilities: set[str]
    ready: bool = False
    last_heartbeat: float = field(default_factory=time.monotonic)
    writer: PeerWriter = field(init=False)

    def __post_init__(self) -> None:
        self.writer = PeerWriter(self.socket.send_text)

    async def send(self, message: Any, *, deadline: float | None = None) -> None:
        def prepare() -> str:
            assert deadline is not None
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise RACPError(
                    "TIMEOUT",
                    "execution budget elapsed before transport",
                    execution_state="not_started",
                )
            return str(
                message.model_copy(update={"remaining_timeout_ms": remaining}).model_dump_json()
            )

        await self.writer.send(message, prepare=prepare if deadline is not None else None)


class ControlPlane:
    def __init__(
        self, store: GatewayStore, *, trusted_personal: bool = False, approvals_enabled: bool = True
    ) -> None:
        self.store = store
        self.handles = HandleCatalog(store)
        self.browser_events = BrowserEvents(store, self.handles)
        self.jobs = JobCatalog(store)
        self.scheduler = Scheduler(self)
        self.streams = StreamBroker(self)
        self.artifacts: ArtifactCatalog | None = None
        self.trusted_personal = trusted_personal
        self.approvals_enabled = approvals_enabled
        self.connections: dict[str, Connection] = {}
        self.events: dict[str, asyncio.Event] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.pending_tasks: set[asyncio.Task[Any]] = set()

    def public(self, record: dict[str, Any], owner: str) -> dict[str, Any]:
        if record["request"]["context"]["principal_id"] != owner:
            raise RACPError("PERMISSION_DENIED", "operation belongs to another principal")
        self.store.device(record["device_id"], owner)
        self.store.require_outcome(record)
        return {
            "operation_id": record["id"],
            "device_id": record["device_id"],
            "operation": record["request"]["operation"],
            "state": record["state"],
            "result": record["result"],
            "error": record["error"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "job_id": (self.jobs.by_operation(record["id"]) or {}).get("id"),
        }

    def get(self, operation_id: str, owner: str) -> dict[str, Any]:
        return self.public(self.store.get(operation_id), owner)

    async def execute(self, input: OperationInput, owner: str) -> dict[str, Any]:
        device = self.store.device(input.device_id, owner)
        if device["revoked"]:
            raise RACPError("DEVICE_REVOKED", "device was revoked")
        payload = validate_payload(input.operation, input.payload)
        spec = REGISTRY[input.operation]
        if (
            input.operation == "filesystem.move"
            and payload.get("copy_and_delete")
            and input.execution_mode != "job"
        ):
            raise RACPError("INVALID_ARGUMENT", "copy-and-delete move requires execution_mode=job")
        if spec.side_effect and input.idempotency_key is None:
            raise RACPError("INVALID_ARGUMENT", "mutation requires idempotency_key")
        idempotency_key = input.idempotency_key or new_id("read")
        decision = evaluate(input.operation, profile_rules(input.execution_profile_id))
        if (
            decision == Decision.DENY
            or input.execution_profile_id == "trusted_personal"
            and not self.trusted_personal
            or decision == Decision.REQUIRE_APPROVAL
            and not self.approvals_enabled
        ):
            self.store.audit("policy_denied", input.model_dump())
            raise RACPError("PERMISSION_DENIED", "execution policy denied request")
        normalized = {
            "principal_id": owner,
            "device_id": input.device_id,
            "operation": input.operation,
            "payload": payload,
            "profile": input.execution_profile_id,
            "policy_revision": 1,
            "timeout_ms": input.budget_ms,
            "execution_mode": input.execution_mode,
        }
        if input.workspace_id != "default":
            normalized["workspace_id"] = input.workspace_id
        payload_hash = canonical_digest(normalized)
        scope = digest(owner + ":" + input.device_id)
        request = Request(
            device_id=input.device_id,
            agent_boot_id="boot_pending",
            connection_epoch=1,
            request_id=new_id("req"),
            operation_id=new_id("op"),
            trace_id=uuid.uuid4().hex,
            timestamp=timestamp(),
            operation=input.operation,
            timeout_ms=input.budget_ms,
            remaining_timeout_ms=input.budget_ms,
            execution_mode=input.execution_mode,
            idempotency_key=idempotency_key,
            context=Context(
                principal_id=owner,
                workspace_id=input.workspace_id,
                execution_profile_id=input.execution_profile_id,
                policy_revision=1,
            ),
            payload=payload,
            retain_key=spec.side_effect
            or input.idempotency_key is not None
            or input.execution_mode == "job",
        ).model_dump()
        record, fresh = self.store.accept(scope, digest(idempotency_key), payload_hash, request)
        if record["state"] in TERMINAL_STATES:
            return (
                self.jobs.acceptance(record["id"], owner)
                if input.execution_mode == "job"
                else self.public(record, owner)
            )
        operation_id = str(record["id"])
        lock = self.locks.setdefault(operation_id, asyncio.Lock())
        async with lock:
            record = self.store.get(operation_id)
            if record["state"] == "ACCEPTED":
                admission = self.store.db.execute(
                    "SELECT 1 FROM operation_admission WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if admission is not None:
                    pass
                else:
                    await self.admit(record, input, owner, decision, payload_hash)
        self.scheduler.wake.set()
        if input.execution_mode == "job":
            return self.jobs.acceptance(operation_id, owner)
        event = self.events.setdefault(operation_id, asyncio.Event())
        if self.store.get(operation_id)["state"] not in TERMINAL_STATES:
            try:
                await asyncio.wait_for(event.wait(), input.budget_ms / 1000 + 7)
            except TimeoutError as exc:
                raise RACPError(
                    "EXECUTION_UNKNOWN",
                    "result not received; inspect operation status",
                    layer="transport",
                    execution_state="unknown",
                    operation_id=operation_id,
                ) from exc
        return self.get(operation_id, owner)

    async def admit(
        self,
        record: dict[str, Any],
        input: OperationInput,
        owner: str,
        decision: Decision,
        payload_hash: str,
    ) -> None:
        operation_id = record["id"]
        payload = record["request"]["payload"]
        if input.operation in ARTIFACT_INPUT_OPERATIONS and payload.get("artifact_id"):
            if self.artifacts is None:
                raise RACPError("CAPABILITY_UNAVAILABLE", "Artifact catalog is unavailable")
            self.artifacts.ready(payload["artifact_id"], owner)
        if decision == Decision.REQUIRE_APPROVAL:
            approval_id = self.store.approval(operation_id, payload_hash)
            if input.approval_id is None:
                raise RACPError(
                    "APPROVAL_REQUIRED",
                    "owner approval required",
                    approval_id=approval_id,
                    operation_id=operation_id,
                )
        connection = self.connections.get(input.device_id)
        if not connection or not connection.ready:
            raise RACPError("DEVICE_OFFLINE", "device is offline", operation_id=operation_id)
        if input.operation not in connection.capabilities:
            raise RACPError("CAPABILITY_UNAVAILABLE", "device does not expose this operation")
        self.events.setdefault(operation_id, asyncio.Event())
        self.jobs.enqueue(
            record,
            approval_id=input.approval_id if decision == Decision.REQUIRE_APPROVAL else None,
            payload_hash=payload_hash,
        )

    def accept_result(
        self, result: Result, connection: Connection, *, reconcile: bool = False
    ) -> None:
        if (
            result.agent_boot_id != connection.boot_id
            or result.connection_epoch != connection.epoch
        ):
            raise RACPError("STALE_CONNECTION", "result is fenced")
        try:
            record = self.store.get(result.operation_id)
        except RACPError as error:
            if (
                reconcile
                and error.error.code == "OPERATION_NOT_FOUND"
                and self.store.retired_transient_outcome(
                    result.operation_id, result.device_id, result.request_id, result.trace_id
                )
            ):
                return  # Correlated retired read cannot revive output or execute again.
            raise
        if (
            record["device_id"] != result.device_id
            or record["request"]["request_id"] != result.request_id
            or record["request"]["trace_id"] != result.trace_id
        ):
            raise RACPError("STALE_CONNECTION", "result correlation mismatch")
        self.store.transition(
            result.operation_id, result.state, result=result.result, error=result.error
        )
        if self.artifacts is not None and result.outputs:
            self.artifacts.observe_outputs([item.model_dump() for item in result.outputs], record)
        self.handles.result(result.result, result.device_id, connection.boot_id)
        self.events.setdefault(result.operation_id, asyncio.Event()).set()
        self.events.pop(result.operation_id, None)
        self.locks.pop(result.operation_id, None)
        self.scheduler.cancel_epochs.pop(result.operation_id, None)
        self.scheduler.wake.set()
        log_event(
            "operation_result",
            trace_id=result.trace_id,
            request_id=result.request_id,
            device_id=result.device_id,
            operation=record["request"]["operation"],
            state=result.state,
        )

    def accept_expired(self, result: OutcomeExpired, connection: Connection) -> None:
        record = self.store.get(result.operation_id)
        if (
            record["device_id"] != result.device_id
            or record["request"]["request_id"] != result.request_id
            or record["request"]["trace_id"] != result.trace_id
            or result.agent_boot_id != connection.boot_id
            or result.connection_epoch != connection.epoch
        ):
            raise RACPError("STALE_CONNECTION", "expired outcome is fenced")
        if record["state"] not in TERMINAL_STATES:
            self.scheduler.finish(
                record,
                "UNKNOWN",
                RACPError(
                    "EXECUTION_UNKNOWN",
                    "Agent outcome expired; do not re-run this key",
                    execution_state="unknown",
                    operation_id=record["id"],
                ),
            )

    def outputs(self, operation_id: str, owner: str) -> dict[str, Any]:
        self.resolutions(operation_id, owner)
        assert self.artifacts is not None
        return {"operation_id": operation_id, "items": self.artifacts.outputs(operation_id, owner)}

    def resolutions(self, operation_id: str, owner: str) -> dict[str, Any]:
        record = self.store.get(operation_id)
        if record["request"]["context"]["principal_id"] != owner:
            raise RACPError("PERMISSION_DENIED", "operation belongs to another principal")
        self.store.device(record["device_id"], owner)
        return OperationResolutions.model_validate(
            {
                "operation_id": operation_id,
                "state": record["state"],
                "items": self.store.resolutions(operation_id),
            }
        ).model_dump()

    async def cancel(self, operation_id: str, owner: str) -> dict[str, Any]:
        record = self.store.get(operation_id)
        self.public(record, owner)
        if record["state"] in TERMINAL_STATES:
            return self.public(record, owner)
        if record["state"] == "ACCEPTED":
            error = asdict(RACPError("CANCELLED", "cancelled before dispatch").error)
            self.store.transition(operation_id, "CANCELLED", error=error)
            self.events.setdefault(operation_id, asyncio.Event()).set()
            return self.get(operation_id, owner)
        connection = self.connections.get(record["device_id"])
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE operations SET state='CANCEL_REQUESTED' WHERE id=?", (operation_id,)
            )
            self.store.on_transition(record, "CANCEL_REQUESTED")
            self.store.db.execute(
                "UPDATE operation_admission SET cancel_reason=COALESCE(cancel_reason,'requested') "
                "WHERE operation_id=?",
                (operation_id,),
            )
            self.store.audit("cancel_requested", record["request"])
        self.scheduler.wake.set()
        if not connection or not connection.ready:
            return self.get(operation_id, owner)
        admission = self.store.db.execute(
            "SELECT cancel_reason FROM operation_admission WHERE operation_id=?", (operation_id,)
        ).fetchone()
        self.store.db.execute(
            "UPDATE operation_admission SET cancel_sent=? WHERE operation_id=?",
            (time.monotonic(), operation_id),
        )
        await connection.send(
            Cancel(
                device_id=record["device_id"],
                agent_boot_id=connection.boot_id,
                connection_epoch=connection.epoch,
                request_id=new_id("req"),
                target_operation_id=operation_id,
                reason=admission["cancel_reason"] if admission else "requested",
            )
        )
        self.scheduler.cancel_epochs[operation_id] = connection.epoch
        return self.get(operation_id, owner)

    async def cancel_job(self, job_id: str, owner: str) -> dict[str, Any]:
        job = self.jobs.get(job_id, owner)
        await self.cancel(job["operation_id"], owner)
        return self.jobs.get(job_id, owner)

    def job_update(
        self, message: JobUpdate, connection: Connection, *, reconcile: bool = False
    ) -> None:
        record = self.store.get(message.operation_id)
        if (
            record["device_id"] != message.device_id
            or record["request"]["request_id"] != message.request_id
            or record["request"]["trace_id"] != message.trace_id
            or message.agent_boot_id != connection.boot_id
            or message.connection_epoch != connection.epoch
        ):
            raise RACPError("STALE_CONNECTION", "job event is fenced")
        self.jobs.update(message, reconcile=reconcile)

    def reconcile_active(
        self, snapshots: list[ExecutionSnapshot], device: str, connection: Connection
    ) -> None:
        seen = set()
        for snapshot in snapshots:
            record = self.store.get(snapshot.operation_id)
            if (
                record["device_id"] != device
                or record["request"]["request_id"] != snapshot.request_id
                or record["request"]["trace_id"] != snapshot.trace_id
            ):
                raise RACPError("STALE_CONNECTION", "active execution correlation differs")
            seen.add(snapshot.operation_id)
            if record["state"] in TERMINAL_STATES:
                continue
            admission = self.store.db.execute(
                "SELECT * FROM operation_admission WHERE operation_id=?",
                (snapshot.operation_id,),
            ).fetchone()
            if admission and admission["state"] != "SENT":
                raise RACPError("STALE_CONNECTION", "active operation was never dispatched")
            pending_cancel = (
                record["state"] == "CANCEL_REQUESTED"
                or snapshot.state == "CANCEL_REQUESTED"
                or (admission is not None and admission["cancel_reason"] is not None)
            )
            self.store.transition(
                snapshot.operation_id, "CANCEL_REQUESTED" if pending_cancel else "RUNNING"
            )
            if snapshot.job_state is not None and snapshot.progress_revision > 0:
                self.job_update(
                    JobUpdate(
                        device_id=device,
                        agent_boot_id=connection.boot_id,
                        connection_epoch=connection.epoch,
                        request_id=snapshot.request_id,
                        operation_id=snapshot.operation_id,
                        trace_id=snapshot.trace_id,
                        state=snapshot.job_state,
                        waiting_reason=snapshot.waiting_reason,
                        progress=snapshot.progress,
                        revision=snapshot.progress_revision,
                    ),
                    connection,
                    reconcile=True,
                )
        for record in self.store.iter_records(device, nonterminal=True):
            if record["state"] == "RECONCILING" and record["id"] not in seen:
                self.scheduler.finish(
                    record,
                    "UNKNOWN",
                    RACPError(
                        "EXECUTION_UNKNOWN",
                        "dispatched operation absent from Agent inventory; inspect before retry",
                        execution_state="unknown",
                    ),
                )

    async def revoke(self, device_id: str, owner: str) -> None:
        self.store.device(device_id, owner)
        connection = self.connections.get(device_id)
        if connection:
            for record in self.store.iter_records(device_id, nonterminal=True):
                if record["state"] not in TERMINAL_STATES and record["state"] != "ACCEPTED":
                    await self.cancel(record["id"], owner)
        self.store.revoke(device_id)
        if connection:
            self.streams.disconnected(device_id, connection, "DEVICE_REVOKED")
            await connection.socket.close(code=4003, reason="DEVICE_REVOKED")

    def audit_rows(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.store.db.execute(
                "SELECT * FROM audit WHERE owner_id='owner_local' ORDER BY timestamp DESC LIMIT 500"
            ).fetchall()
        ]


def artifact_digest(path: Any) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
