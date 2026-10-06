import asyncio
import getpass
import json
import os
import platform
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psutil
from racp_domain.models import TERMINAL_STATES, ExecutionContext, RACPError
from racp_observability.logging import log_event
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.artifacts import ARTIFACT_INPUT_OPERATIONS
from racp_protocol.models import (
    Ack,
    BrowserState,
    BrowserStateAck,
    Cancel,
    Capability,
    ExecutionSnapshot,
    Heartbeat,
    Hello,
    JobUpdate,
    OutcomeExpired,
    Reconcile,
    Request,
    ResourceHandle,
    Result,
    Welcome,
    decode_message,
    new_id,
)
from racp_protocol.provider_models import SENSITIVE_PROCESS_READS
from racp_protocol.registry import REGISTRY, validate_payload
from racp_protocol.streams import StreamAck, StreamSubscribe, StreamUnsubscribe
from racp_sdk.artifacts import ArtifactClient, file_digest
from racp_sdk.journal import Journal
from racp_sdk.peer_writer import PeerWriter
from racp_sdk.security import (
    canonical_digest,
    digest,
    require_secure_url,
    tls_context,
    websocket_tls_options,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from racp_agent.browser_events import BrowserOutbox
from racp_agent.outputs import OutputSpool
from racp_agent.plugins.config import PluginConfig
from racp_agent.providers.browser import BrowserProvider
from racp_agent.providers.desktop import DesktopProvider
from racp_agent.providers.filesystem import FilesystemProvider
from racp_agent.providers.process import ProcessProvider
from racp_agent.providers.reversing import ReversingProvider
from racp_agent.providers.shell import ShellProvider
from racp_agent.providers.terminal import TerminalProvider
from racp_agent.terminal_streams import TerminalStreams
from racp_agent.workspaces import WorkspaceSpec


class Agent:
    def __init__(
        self,
        gateway: str,
        credential: str,
        device_id: str,
        workspace: Path,
        data_dir: Path,
        *,
        profile: str = "read_only",
        browser_allowed_origins: tuple[str, ...] = (),
        browser_cdp: bool = False,
        desktop_sessions: tuple[int, ...] = (),
        desktop_login_users: tuple[str, ...] = (),
        service_sid: str | None = None,
        plugins: PluginConfig | None = None,
        ca_file: Path | None = None,
        allowed_workspaces: tuple[WorkspaceSpec, ...] = (),
    ) -> None:
        require_secure_url(gateway)
        self.gateway = gateway.rstrip("/")
        self.ca_file, self.ssl = ca_file, tls_context(ca_file)
        self.credential, self.device_id = credential, device_id
        self.boot_id = new_id("boot")
        self.profile = profile
        self.journal = Journal(data_dir / "execution.db")
        self.journal.recover_agent()
        self.provider = ShellProvider(workspace, data_dir / "spool", allowed_workspaces)
        self.filesystem = FilesystemProvider(workspace, data_dir / "spool", allowed_workspaces)
        self.processes = ProcessProvider(self.provider)
        self.terminals = TerminalProvider(self.provider)
        self.desktop = DesktopProvider(
            data_dir / "brokers",
            data_dir / "spool",
            device_id,
            desktop_sessions,
            desktop_login_users,
            service_sid,
        )
        self.reversing = ReversingProvider(
            workspace,
            data_dir / "re",
            data_dir / "spool",
            plugins or PluginConfig(),
            allowed_workspaces,
        )
        self.processes.extra_protected_pids = lambda: (
            self.desktop.protected_pids() | self.reversing.protected_pids()
        )
        self.reversing.protected = lambda: {
            os.getpid(),
            *[p.pid for p in psutil.Process().parents()],
            *self.desktop.protected_pids(),
            *self.reversing.protected_pids(),
        }
        self.browsers = BrowserProvider(
            data_dir / "spool", allowed_origins=browser_allowed_origins, cdp_enabled=browser_cdp
        )
        self.spool = data_dir / "spool"
        self.outputs = OutputSpool(self.spool, self.journal)
        self.outputs.external_usage = lambda: (
            sum(s.disk_bytes for s in self.browsers.sessions.values()) + self.reversing.disk_bytes()
        )
        self.browser_events = BrowserOutbox()
        self.browsers.on_event = self.browser_event
        self.socket: ClientConnection | None = None
        self.epoch = 0
        self.connection_phase = "starting"
        self.lease_expires = 0.0
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.deadlines: dict[str, float] = {}
        self.cancel_reasons: dict[str, str] = {}
        self.journal.db.execute("""CREATE TABLE IF NOT EXISTS execution_progress (
            operation_id TEXT PRIMARY KEY, state TEXT NOT NULL, waiting_reason TEXT,
            progress REAL, revision INTEGER NOT NULL)""")
        self.writer: PeerWriter | None = None
        self.streams: TerminalStreams | None = None
        self.stopping = asyncio.Event()
        self.cleanup_complete = False
        self.desktop.allow_registration = lambda: (
            not self.stopping.is_set() and self.lease_expires > time.monotonic()
        )

    async def send(self, message: Any) -> None:
        if self.writer is None:
            raise ConnectionError("agent is disconnected")
        await self.writer.send(message)

    def browser_event(self, session: Any, kind: str, state: str) -> None:
        handles = [
            self.browsers.handle(session),
            *[self.browsers.handle(session, id) for id in session.pages],
        ]
        self.browser_events.push(
            {"browser_id": session.id, "kind": kind, "state": state, "handles": handles},
            self.browsers.inventory(),
        )

    def browser_capability(self) -> Capability:
        health = self.browsers.health()
        native = bool(health["native_browser_available"])
        ready = bool(
            health["installed"]
            and health["supported"]
            and (
                native
                or self.browsers.cdp_enabled
                or any(s.state == "ACTIVE" for s in self.browsers.sessions.values())
            )
        )
        return Capability(
            name="browser",
            version="1.0.0",
            installed=bool(health["installed"]),
            supported=bool(health["supported"]),
            healthy=ready,
            unavailable_reason=None if ready else "BROWSER_RUNTIME_UNAVAILABLE",
            operations=[
                name
                for name in REGISTRY
                if name.startswith("browser.")
                and (
                    self.browsers.cdp_enabled
                    or name not in {"browser.attach", "browser.cdp_targets"}
                )
                and (native or name != "browser.open")
            ],
            attributes={
                **health,
                "backend": "playwright-python",
                "version": "1.63.0",
                "sandbox": True,
                "profile": "isolated_ephemeral",
                "context_limit": 4,
                "page_limit": 8,
                "idle_ttl_seconds": 3600,
                "allowed_origins": self.browsers.allowed_origins,
                "cdp_enabled": self.browsers.cdp_enabled,
                "evaluate_max_bytes": 65536,
                "containment": "windows-job-object"
                if platform.system() == "Windows"
                else "process-group",
            },
        )

    async def browser_event_sender(self) -> None:
        last_probe, last_capability = 0.0, ""
        while not self.stopping.is_set():
            await asyncio.sleep(0.05)
            if time.monotonic() - last_probe >= 10:
                last_probe = time.monotonic()
                capability = self.browser_capability()
                serialized = capability.model_dump_json()
                if serialized != last_capability:
                    self.browser_events.push(
                        {
                            "kind": "health",
                            "state": "files_verified" if capability.healthy else "unavailable",
                            "capability": capability.model_dump(),
                        },
                        self.browsers.inventory(),
                    )
                    last_capability = serialized
            if (
                not self.browser_events.pending
                or self.writer is None
                or time.monotonic() >= self.lease_expires
            ):
                continue
            pending = dict(self.browser_events.pending[0])
            message = BrowserState.model_validate(
                {
                    "device_id": self.device_id,
                    "agent_boot_id": self.boot_id,
                    "connection_epoch": self.epoch,
                    "provider_instance_id": self.browsers.instance_id,
                    **pending,
                }
            )
            self.browser_events.sent = max(self.browser_events.sent, int(message.event_sequence))
            try:
                await self.send(message)
                # An event ACK is independent of executing/consuming command output.
                for _ in range(20):
                    if self.browser_events.acked >= int(message.event_sequence):
                        break
                    await asyncio.sleep(0.05)
            except (ConnectionError, ConnectionClosed, RACPError):
                continue

    def result_message(self, record: dict[str, Any]) -> Result | OutcomeExpired:
        request = record["request"]
        if not record["outcome_available"]:
            return OutcomeExpired(
                device_id=self.device_id,
                agent_boot_id=self.boot_id,
                connection_epoch=self.epoch,
                request_id=request["request_id"],
                operation_id=record["id"],
                trace_id=request["trace_id"],
            )
        return Result(
            device_id=self.device_id,
            agent_boot_id=self.boot_id,
            connection_epoch=self.epoch,
            request_id=request["request_id"],
            operation_id=record["id"],
            trace_id=request["trace_id"],
            state=record["state"],
            result=record["result"],
            error=record["error"],
            outputs=self.outputs.descriptors(record["id"]),
        )

    async def _upload(self, path: Path, operation_id: str, media_type: str) -> str:
        row = self.journal.db.execute(
            "SELECT id FROM output_spool WHERE operation_id=? AND filename=? "
            "AND artifact_id IS NULL",
            (operation_id, path.name),
        ).fetchone()
        output = self.outputs.get(row["id"]) if row else None
        client = ArtifactClient(
            self.gateway,
            self.credential,
            self.device_id,
            operation_id=operation_id,
            output_id=output["id"] if output else None,
            ca_file=self.ca_file,
        )
        try:
            result = await client.upload(
                path,
                media_type=media_type,
                transfer_id=output["transfer_id"] if output else None,
                transfer_created=(
                    lambda transfer: self.outputs.set_transfer(output["id"], transfer)
                )
                if output
                else None,
            )
        except RACPError as exc:
            if output:
                self.outputs.failed(output["id"], exc.error.code)
                if exc.error.code == "ARTIFACT_EXPIRED":
                    self.journal.db.execute(
                        "UPDATE output_spool SET transfer_id=NULL WHERE id=?", (output["id"],)
                    )
            raise
        if output:
            self.outputs.completed(output["id"], str(result["id"]))
        return str(result["id"])

    async def prepare_output(
        self, path: Path, operation_id: str, media_type: str
    ) -> dict[str, Any]:
        async def prepare() -> dict[str, Any]:
            size, sha256 = await asyncio.to_thread(file_digest, path)
            return self.outputs.register(path, operation_id, media_type, size, sha256)

        preparation = asyncio.create_task(prepare())
        try:
            return await asyncio.shield(preparation)
        except asyncio.CancelledError:
            # Provider completion is already a fact. Finish declaring the bytes so
            # a deadline or owner cancel cannot erase them or trigger re-execution.
            return await asyncio.shield(preparation)

    async def execute(self, request: Request) -> None:
        input_cache: Path | None = None
        deadline = self.deadlines.get(
            request.operation_id, time.monotonic() + request.remaining_timeout_ms / 1000
        )
        context = ExecutionContext(
            request.operation_id,
            request.request_id,
            request.trace_id,
            request.device_id,
            request.context.principal_id,
            self.boot_id,
            max(1, int((deadline - time.monotonic()) * 1000)),
            request.context.workspace_id,
        )
        try:
            self.filesystem.guard.get(context.workspace_id)
            self.outputs.reserve(request.operation_id, request.operation, request.payload)
            if (
                evaluate(request.operation, profile_rules(self.profile)) == Decision.DENY
                or evaluate(request.operation, profile_rules(request.context.execution_profile_id))
                == Decision.DENY
            ):
                raise RACPError(
                    "PERMISSION_DENIED", "agent local policy denies operation", layer="agent"
                )
            if (
                request.context.execution_profile_id == "trusted_personal"
                and self.profile != "trusted_personal"
                and REGISTRY[request.operation].side_effect
            ):
                raise RACPError(
                    "PERMISSION_DENIED", "trusted profile not enabled locally", layer="agent"
                )
            if time.monotonic() >= self.lease_expires:
                raise RACPError("DEVICE_OFFLINE", "execution lease expired", layer="agent")
            if time.monotonic() >= deadline:
                raise RACPError(
                    "TIMEOUT", "remaining execution budget elapsed before provider", layer="agent"
                )
            self.journal.transition(request.operation_id, "RUNNING")
            log_event(
                "agent_execution_started",
                device_id=self.device_id,
                operation=request.operation,
                operation_id=request.operation_id,
                state="RUNNING",
            )
            if request.execution_mode == "job":
                await self.job_progress(
                    request,
                    "WAITING" if request.operation == "process.wait" else "RUNNING",
                    waiting_reason="process_exit" if request.operation == "process.wait" else None,
                )
            payload = request.payload
            if request.operation in ARTIFACT_INPUT_OPERATIONS and payload.get("artifact_id"):
                input_cache = self.spool / (request.operation_id + ".input")
                client = ArtifactClient(
                    self.gateway,
                    self.credential,
                    self.device_id,
                    operation_id=request.operation_id,
                    ca_file=self.ca_file,
                )
                started = time.monotonic()
                try:
                    async with asyncio.timeout(context.timeout_ms / 1000):
                        artifact = await client.download(payload["artifact_id"], input_cache)
                except TimeoutError as exc:
                    raise RACPError(
                        "TIMEOUT", "Artifact input download timed out", layer="agent"
                    ) from exc
                elapsed = int((time.monotonic() - started) * 1000)
                context = replace(context, timeout_ms=max(1, context.timeout_ms - elapsed))
                payload = {
                    **payload,
                    "_artifact_path": str(input_cache),
                    "_artifact_sha256": artifact["sha256"],
                    "_artifact_size": artifact["size_bytes"],
                }
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise RACPError(
                    "TIMEOUT", "execution budget elapsed before provider", layer="agent"
                )
            context = replace(context, timeout_ms=remaining)
            if request.operation.startswith("filesystem."):
                outcome = await self.filesystem.execute(request.operation, payload, context)
            elif request.operation.startswith("terminal."):
                outcome = await self.terminals.execute(request.operation, payload, context)
            elif request.operation.startswith("process."):
                outcome = await self.processes.execute(request.operation, request.payload, context)
            elif request.operation.startswith("browser."):
                outcome = await self.browsers.execute(request.operation, payload, context)
            elif request.operation.startswith("desktop."):
                outcome = await self.desktop.execute(request.operation, payload, context)
            elif request.operation.startswith(("re.", "debugger.")):
                outcome = await self.reversing.execute(request.operation, payload, context)
            else:
                outcome = await self.provider.execute(request.operation, request.payload, context)
            if (
                outcome["state"] == "CANCELLED"
                and self.cancel_reasons.get(request.operation_id) == "deadline"
            ):
                outcome["state"] = "TIMED_OUT"
                outcome["error"] = asdict(
                    RACPError(
                        "TIMEOUT",
                        "Gateway execution deadline elapsed",
                        layer="agent",
                        cleanup_status=(outcome.get("result") or {}).get(
                            "cleanup_status", "unknown"
                        ),
                    ).error
                )
            result = outcome.get("result")
            if (
                result
                and not result.get("spool_path")
                and len(json.dumps(result, ensure_ascii=False).encode()) > 65536
            ):
                spool = self.spool / (request.operation_id + ".json")
                await asyncio.to_thread(
                    spool.write_text, json.dumps(result, ensure_ascii=False), encoding="utf-8"
                )
                outcome["result"] = result = {
                    "artifact_id": None,
                    "spool_path": str(spool),
                    "artifact_media_type": "application/json",
                    "truncated": True,
                }
            if result and result.get("spool_path"):
                path = Path(result.pop("spool_path"))
                upload: asyncio.Task[str] | None = None
                try:
                    media_type = result.get(
                        "artifact_media_type", "application/vnd.racp.output-stream"
                    )
                    output = await self.prepare_output(path, request.operation_id, media_type)
                    result["output_id"] = output["id"]
                    upload = asyncio.create_task(
                        self._upload(path, request.operation_id, media_type)
                    )
                    try:
                        async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                            result["artifact_id"] = await asyncio.shield(upload)
                    except asyncio.CancelledError:
                        upload.cancel()
                        await asyncio.gather(upload, return_exceptions=True)
                        raise RACPError(
                            "TRANSFER_INTERRUPTED",
                            "completed output retained for Artifact retry",
                            layer="agent",
                        ) from None
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                except (httpx.HTTPError, OSError, RACPError, TimeoutError) as upload_error:
                    if upload is not None and not upload.done():
                        upload.cancel()
                        await asyncio.gather(upload, return_exceptions=True)
                    result["artifact_upload_status"] = "pending"
                    result["artifact_upload_error"] = (
                        asdict(upload_error.error)
                        if isinstance(upload_error, RACPError)
                        else {"code": "TRANSFER_INTERRUPTED", "message": "Artifact upload failed"}
                    )
            if result and (outcome.get("error") or {}).get("details", {}).get("partial_result"):
                outcome["error"]["details"]["partial_result"] = dict(result)
            if (
                result
                and isinstance(result.get("preview"), dict)
                and result["preview"].get("spool_path")
            ):
                preview = result["preview"]
                preview_path = Path(preview.pop("spool_path"))
                media_type = preview.pop("artifact_media_type", "image/png")
                output = await self.prepare_output(preview_path, request.operation_id, media_type)
                preview["output_id"] = output["id"]
                preview_upload = asyncio.create_task(
                    self._upload(preview_path, request.operation_id, media_type)
                )
                try:
                    async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                        preview["artifact_id"] = await asyncio.shield(preview_upload)
                    await asyncio.to_thread(preview_path.unlink, missing_ok=True)
                except (httpx.HTTPError, OSError, RACPError, TimeoutError, asyncio.CancelledError):
                    preview_upload.cancel()
                    await asyncio.gather(preview_upload, return_exceptions=True)
                    preview["artifact_upload_status"] = "pending"
            self.journal.transition(
                request.operation_id, outcome["state"], result=result, error=outcome.get("error")
            )
        except asyncio.CancelledError:
            self.journal.transition(
                request.operation_id,
                "TIMED_OUT"
                if self.cancel_reasons.get(request.operation_id) == "deadline"
                else "CANCELLED",
                error=asdict(
                    RACPError(
                        "TIMEOUT"
                        if self.cancel_reasons.get(request.operation_id) == "deadline"
                        else "CANCELLED",
                        "execution interrupted",
                        layer="agent",
                        execution_state="unknown"
                        if request.operation.startswith("desktop.")
                        else "not_started",
                        **(
                            {
                                "cleanup_status": self.desktop.sessions[
                                    request.payload["session_id"]
                                ].cleanup_status
                            }
                            if request.operation.startswith("desktop.")
                            and "session_id" in request.payload
                            and request.payload["session_id"] in self.desktop.sessions
                            else {}
                        ),
                    ).error
                ),
            )
        except RACPError as exc:
            error = asdict(exc.error)
            report_path = error["details"].pop("report_spool_path", None)
            if report_path:
                try:
                    output = await self.prepare_output(
                        Path(report_path), request.operation_id, "application/json"
                    )
                    error["details"]["report_output_id"] = output["id"]
                    async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                        error["details"]["report_artifact_id"] = await self._upload(
                            Path(report_path), request.operation_id, "application/json"
                        )
                    await asyncio.to_thread(Path(report_path).unlink, missing_ok=True)
                except (httpx.HTTPError, OSError, RACPError, TimeoutError, asyncio.CancelledError):
                    error["details"]["report_upload_status"] = "pending"
            state = {
                "TIMEOUT": "TIMED_OUT",
                "CANCELLED": "CANCELLED",
                "EXECUTION_UNKNOWN": "UNKNOWN",
            }.get(exc.error.code, "FAILED")
            self.journal.transition(request.operation_id, state, error=error)
        except Exception:
            self.journal.transition(
                request.operation_id,
                "UNKNOWN",
                error=asdict(
                    RACPError(
                        "EXECUTION_UNKNOWN",
                        "provider failed without recoverable result",
                        layer="agent",
                        execution_state="unknown",
                    ).error
                ),
            )
        finally:
            self.outputs.release(request.operation_id)
            self.deadlines.pop(request.operation_id, None)
            self.cancel_reasons.pop(request.operation_id, None)
            if input_cache is not None:
                await asyncio.to_thread(input_cache.unlink, missing_ok=True)
            record = self.journal.get(request.operation_id)
            log_event(
                "agent_result",
                operation_id=request.operation_id,
                trace_id=request.trace_id,
                request_id=request.request_id,
                device_id=self.device_id,
                operation=request.operation,
                state=record["state"],
            )
            if self.socket is not None:
                try:
                    await self.send(self.result_message(record))
                except (ConnectionError, ConnectionClosed):
                    pass

    async def dispatch(self, request: Request) -> None:
        received_deadline = time.monotonic() + request.remaining_timeout_ms / 1000
        validate_payload(request.operation, request.payload)
        normalized: dict[str, Any] = {
            "operation": request.operation,
            "payload": request.payload,
            "context": request.context.model_dump(),
            "timeout_ms": request.timeout_ms,
            "execution_mode": request.execution_mode,
        }
        if request.context.workspace_id == "default":
            normalized["context"].pop("workspace_id", None)
        try:
            record, fresh = self.journal.accept(
                digest(request.context.principal_id + ":" + self.device_id),
                digest(request.idempotency_key),
                canonical_digest(normalized),
                request.model_dump(),
            )
        except RACPError as exc:
            if exc.error.code != "OPERATION_EXPIRED":
                raise
            record = self.journal.get(exc.error.details["operation_id"])
            if record["id"] != request.operation_id:
                raise RACPError(
                    "IDEMPOTENCY_CONFLICT", "expired key belongs to another operation"
                ) from exc
            await self.send(self.result_message(record))
            return
        if not fresh and record["id"] != request.operation_id:
            raise RACPError("IDEMPOTENCY_CONFLICT", "key bound to another operation", layer="agent")
        await self.send(
            Ack(
                device_id=self.device_id,
                agent_boot_id=self.boot_id,
                connection_epoch=self.epoch,
                request_id=request.request_id,
                operation_id=request.operation_id,
                trace_id=request.trace_id,
            )
        )
        if record["state"] in TERMINAL_STATES:
            await self.send(self.result_message(record))
        elif fresh:
            if sum(self.journal.get(id)["state"] not in TERMINAL_STATES for id in self.tasks) >= 16:
                self.journal.transition(
                    request.operation_id,
                    "FAILED",
                    error=asdict(
                        RACPError(
                            "RESOURCE_EXHAUSTED",
                            "agent execution concurrency limit reached",
                            layer="agent",
                        ).error
                    ),
                )
                await self.send(self.result_message(self.journal.get(request.operation_id)))
                return
            self.deadlines[request.operation_id] = received_deadline
            task = asyncio.create_task(self.execute(request))
            self.tasks[request.operation_id] = task
            task.add_done_callback(lambda _: self.tasks.pop(request.operation_id, None))
            # Start the execution wrapper before a following cancel can cancel an unstarted task.
            await asyncio.sleep(0)

    async def job_progress(
        self, request: Request, state: str, *, waiting_reason: str | None = None
    ) -> None:
        row = self.journal.db.execute(
            "SELECT revision FROM execution_progress WHERE operation_id=?", (request.operation_id,)
        ).fetchone()
        revision = row["revision"] + 1 if row else 1
        with self.journal.transaction():
            self.journal.db.execute(
                "INSERT INTO execution_progress VALUES (?,?,?,NULL,?) "
                "ON CONFLICT(operation_id) DO UPDATE SET state=excluded.state,"
                "waiting_reason=excluded.waiting_reason,"
                "revision=excluded.revision",
                (request.operation_id, state, waiting_reason, revision),
            )
            self.journal.audit(
                "job_state_changed",
                request.model_dump(),
                state=state,
                waiting_reason=waiting_reason,
            )
        update = JobUpdate.model_validate(
            {
                "device_id": self.device_id,
                "agent_boot_id": self.boot_id,
                "connection_epoch": self.epoch,
                "request_id": request.request_id,
                "operation_id": request.operation_id,
                "trace_id": request.trace_id,
                "state": state,
                "waiting_reason": waiting_reason,
                "revision": revision,
            }
        )
        try:
            await self.send(update)
        except (OSError, ConnectionClosed):
            pass  # Reconciliation carries the committed progress observation.

    def execution_inventory(self) -> list[ExecutionSnapshot]:
        snapshots = []
        for operation_id in list(self.tasks):
            record = self.journal.get(operation_id)
            if record["state"] in TERMINAL_STATES:
                continue
            progress = self.journal.db.execute(
                "SELECT * FROM execution_progress WHERE operation_id=?", (operation_id,)
            ).fetchone()
            snapshots.append(
                ExecutionSnapshot.model_validate(
                    {
                        "operation_id": operation_id,
                        "request_id": record["request"]["request_id"],
                        "trace_id": record["request"]["trace_id"],
                        "state": record["state"],
                        "job_state": progress["state"] if progress else None,
                        "waiting_reason": progress["waiting_reason"] if progress else None,
                        "progress": progress["progress"] if progress else None,
                        "progress_revision": progress["revision"] if progress else 0,
                    }
                )
            )
        return snapshots

    async def heartbeat(self, interval_ms: int) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(interval_ms / 1000)
            await self.send(
                Heartbeat(
                    device_id=self.device_id,
                    agent_boot_id=self.boot_id,
                    connection_epoch=self.epoch,
                    handles=self.inventory(),
                    capabilities=[self.desktop.capability(), *self.reversing.capabilities()],
                )
            )

    async def desktop_health(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(5)
            if self.lease_expires > time.monotonic():
                await self.desktop.probe()

    async def plugin_health(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(5)
            if self.lease_expires > time.monotonic():
                await self.reversing.probe()

    async def plugin_events(self) -> None:
        while not self.stopping.is_set():
            self.reversing.process_events()
            if (
                self.reversing.changed.is_set()
                and self.writer is not None
                and self.lease_expires > time.monotonic()
            ):
                self.reversing.changed.clear()
                try:
                    await self.send(
                        Heartbeat(
                            device_id=self.device_id,
                            agent_boot_id=self.boot_id,
                            connection_epoch=self.epoch,
                            handles=self.inventory(),
                            capabilities=self.reversing.capabilities(),
                        )
                    )
                except (ConnectionError, ConnectionClosed, RACPError):
                    self.reversing.changed.set()
            await asyncio.sleep(0.1)

    def inventory(self) -> list[ResourceHandle]:
        handles = [self.terminals.handle(item) for item in list(self.terminals.sessions.values())]
        handles.extend(self.browsers.inventory())
        handles.extend(handle.model_dump() for handle in self.reversing.inventory())
        handles.extend(
            item.handle(self.device_id, self.processes.instance_id)
            for item in list(self.processes.managed.values())
        )
        handles.sort(key=lambda handle: handle["state"] not in {"ACTIVE", "CREATING", "CLOSING"})
        return [ResourceHandle.model_validate(handle) for handle in handles[:128]]

    async def lease_watchdog(self) -> None:
        last_maintenance = time.monotonic()
        while not self.stopping.is_set():
            await asyncio.sleep(0.25)
            if time.monotonic() - last_maintenance >= 60:
                self.journal.compact(
                    pinned_operations=set(self.tasks) | self.outputs.pinned_operations()
                )
                last_maintenance = time.monotonic()
            if self.lease_expires and time.monotonic() >= self.lease_expires:
                for task in list(self.tasks.values()):
                    if not task.cancelling():
                        task.cancel()
                await self.processes.cleanup()
                await self.terminals.cleanup()
                await self.browsers.cleanup()
                await self.desktop.cleanup()
                await self.reversing.cleanup()
            else:
                await self.processes.cleanup(expired_only=True)
                await self.terminals.cleanup(expired_only=True)
                await self.browsers.cleanup(expired_only=True)
                await self.reversing.cleanup(expired_only=True)

    async def session(self) -> None:
        self.connection_phase = "connecting"
        parsed = urlsplit(self.gateway)
        ws_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                "/agent/v1/connect",
                "",
                "",
            )
        )
        require_secure_url(ws_url, websocket=True)
        async with connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {self.credential}"},
            compression=None,
            max_size=1024 * 1024,
            open_timeout=10,
            **websocket_tls_options(ws_url, self.ssl),
        ) as socket:
            self.socket = socket
            self.writer = PeerWriter(socket.send)
            try:
                self.connection_phase = "hello"
                await self.send(
                    Hello(
                        device_id=self.device_id,
                        agent_boot_id=self.boot_id,
                        agent_version="0.1.0",
                        supported_protocols=[1],
                        platform=platform.system(),
                        architecture=platform.machine(),
                        execution_identity=getpass.getuser(),
                        capabilities=[
                            self.browser_capability(),
                            self.desktop.capability(),
                            *self.reversing.capabilities(),
                            Capability(
                                name="shell",
                                version="1.0.0",
                                operations=["shell.exec"],
                                attributes={
                                    "containment": "windows-job-object"
                                    if platform.system() == "Windows"
                                    else "process-group",
                                    "local_profile": self.profile,
                                    "workspaces": self.filesystem.guard.inventory(),
                                    "shell_access": "os_account",
                                },
                            ),
                            Capability(
                                name="filesystem",
                                version="1.0.0",
                                operations=[
                                    name for name in REGISTRY if name.startswith("filesystem.")
                                ],
                                attributes={
                                    "workspace": str(self.filesystem.guard.root),
                                    "workspaces": self.filesystem.guard.inventory(),
                                    "link_policy": "no_follow",
                                    "external_writer_atomic_cas": False,
                                },
                            ),
                            Capability(
                                name="terminal",
                                version="1.0.0",
                                operations=[
                                    name for name in REGISTRY if name.startswith("terminal.")
                                ],
                                attributes={
                                    "backend": "ConPTY"
                                    if platform.system() == "Windows"
                                    else "pty",
                                    "ring_bytes": 4 * 1024 * 1024,
                                    "session_limit": 8,
                                    "streams": "merged",
                                    "idle_ttl_seconds": 8 * 3600,
                                    "push_stream": True,
                                    "stream_window_bytes": 256 * 1024,
                                    "stream_chunk_bytes": 65536,
                                    "stream_limit": 16,
                                },
                            ),
                            Capability(
                                name="process",
                                version="1.0.0",
                                operations=[
                                    name
                                    for name in REGISTRY
                                    if name.startswith("process.")
                                    and (os.name == "nt" or name not in SENSITIVE_PROCESS_READS)
                                ],
                                attributes={
                                    "spawn_output": "discard",
                                    "managed_process_limit": 32,
                                    "memory_read_supported": os.name == "nt",
                                    "memory_read_max_bytes": 16 * 1024 * 1024,
                                    "memory_consistency": "live_process_observation",
                                },
                            ),
                        ],
                    )
                )
                self.connection_phase = "welcome"
                welcome = decode_message(await asyncio.wait_for(socket.recv(), 10))
                if not isinstance(welcome, Welcome) or welcome.device_id != self.device_id:
                    raise ValueError("invalid welcome")
                self.epoch = welcome.connection_epoch
                self.connection_phase = "reconcile"
                for record in self.journal.iter_records(self.device_id):
                    if record["state"] in TERMINAL_STATES:
                        outcome = self.result_message(record)
                        await self.send(
                            Reconcile(
                                device_id=self.device_id,
                                agent_boot_id=self.boot_id,
                                connection_epoch=self.epoch,
                                records=[outcome] if isinstance(outcome, Result) else [],
                                expired=[outcome] if isinstance(outcome, OutcomeExpired) else [],
                                complete=False,
                            )
                        )
                await self.send(
                    Reconcile(
                        device_id=self.device_id,
                        agent_boot_id=self.boot_id,
                        connection_epoch=self.epoch,
                        handles=self.inventory(),
                        active=self.execution_inventory(),
                    )
                )
                accepted = decode_message(await asyncio.wait_for(socket.recv(), 10))
                if not isinstance(accepted, Heartbeat):
                    raise ValueError("reconciliation not acknowledged")
                self.lease_expires = time.monotonic() + welcome.execution_lease_ttl_ms / 1000
                self.connection_phase = "ready"
                log_event(
                    "agent_connected",
                    device_id=self.device_id,
                    agent_boot_id=self.boot_id,
                    connection_epoch=self.epoch,
                    execution_identity=getpass.getuser(),
                    workspace=str(self.filesystem.guard.root),
                    profile=self.profile,
                )
                self.streams = TerminalStreams(
                    self.terminals, self.send, self.profile, lambda: self.lease_expires
                )
                heartbeat = asyncio.create_task(self.heartbeat(welcome.heartbeat_interval_ms))
                try:
                    async for raw in socket:
                        try:
                            message = decode_message(raw)
                            if (
                                message.device_id != self.device_id
                                or message.agent_boot_id != self.boot_id
                                or getattr(message, "connection_epoch", None) != self.epoch
                            ):
                                raise ValueError("stale connection")
                            if isinstance(message, Heartbeat):
                                self.lease_expires = (
                                    time.monotonic() + welcome.execution_lease_ttl_ms / 1000
                                )
                            elif isinstance(message, BrowserStateAck):
                                if message.provider_instance_id != self.browsers.instance_id:
                                    raise RACPError(
                                        "STALE_CONNECTION", "browser ACK provider mismatch"
                                    )
                                self.browser_events.ack(message.event_sequence)
                            elif isinstance(message, Request):
                                await self.dispatch(message)
                            elif isinstance(message, Cancel):
                                task = self.tasks.get(message.target_operation_id)
                                if task and not task.cancelling():
                                    self.cancel_reasons[message.target_operation_id] = (
                                        message.reason
                                    )
                                    self.journal.transition(
                                        message.target_operation_id, "CANCEL_REQUESTED"
                                    )
                                    task.cancel()
                            elif isinstance(message, StreamSubscribe):
                                await self.streams.subscribe(message)
                            elif isinstance(message, StreamAck):
                                self.streams.ack(message)
                            elif isinstance(message, StreamUnsubscribe):
                                await self.streams.unsubscribe(message)
                            else:
                                raise ValueError("unexpected message")
                        except (ValueError, RACPError):
                            await socket.close(code=1008, reason="invalid frame")
                            break
                finally:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            finally:
                if self.streams is not None:
                    await self.streams.close()
                    self.streams = None
                if self.writer is not None:
                    await self.writer.close()
                    self.writer = None
                self.socket = None

    async def run(self) -> None:
        watchdog = asyncio.create_task(self.lease_watchdog())
        output_retry = asyncio.create_task(self.retry_outputs())
        browser_events = asyncio.create_task(self.browser_event_sender())
        desktop_health = asyncio.create_task(self.desktop_health())
        plugin_health = asyncio.create_task(self.plugin_health())
        plugin_events = asyncio.create_task(self.plugin_events())
        backoff = 0.5
        try:
            await self.browsers.recover_remote()
            await self.desktop.start()
            while not self.stopping.is_set():
                try:
                    await self.session()
                    backoff = 0.5
                except (OSError, ConnectionClosed, ValueError) as failure:
                    log_event(
                        "agent_disconnected",
                        device_id=self.device_id,
                        phase=self.connection_phase,
                        exception_type=type(failure).__name__,
                    )
                except InvalidStatus as exc:
                    if exc.response.status_code in {401, 403}:
                        self.stopping.set()
                        break
                    log_event(
                        "agent_handshake_rejected",
                        device_id=self.device_id,
                        http_status=exc.response.status_code,
                    )
                try:
                    await asyncio.wait_for(self.stopping.wait(), backoff)
                except TimeoutError:
                    pass
                backoff = min(10, backoff * 2)
        finally:
            self.stopping.set()
            watchdog.cancel()
            output_retry.cancel()
            browser_events.cancel()
            desktop_health.cancel()
            plugin_health.cancel()
            plugin_events.cancel()
            for task in list(self.tasks.values()):
                if not task.cancelling():
                    task.cancel()
            await asyncio.gather(
                watchdog,
                output_retry,
                browser_events,
                desktop_health,
                plugin_health,
                plugin_events,
                *list(self.tasks.values()),
                return_exceptions=True,
            )
            await self.processes.cleanup()
            await self.terminals.cleanup()
            await self.browsers.cleanup()
            await self.desktop.shutdown()
            await self.reversing.shutdown()
            self.journal.close()
            self.cleanup_complete = True

    async def retry_outputs(self) -> None:
        while not self.stopping.is_set():
            await asyncio.sleep(2)
            self.outputs.collect_completed_files()
            if self.socket is None or self.lease_expires <= time.monotonic():
                continue
            for output in self.outputs.pending():
                if output["operation_id"] in self.tasks:
                    continue
                elapsed = (
                    datetime.now(UTC) - datetime.fromisoformat(output["updated_at"])
                ).total_seconds()
                if elapsed < min(60, 2 ** min(output["attempts"], 6)):
                    continue
                record = self.journal.get(output["operation_id"])
                if record["state"] not in TERMINAL_STATES or not record["outcome_available"]:
                    continue
                path = self.spool / output["filename"]
                try:
                    # Resend the durable declaration first; HTTP and WS processing may
                    # race, so a denied first attempt remains pending for the next pass.
                    await self.send(self.result_message(record))
                    async with asyncio.timeout(30):
                        await self._upload(path, output["operation_id"], output["media_type"])
                    await self.send(self.result_message(record))
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                except (
                    RACPError,
                    OSError,
                    httpx.HTTPError,
                    ConnectionError,
                    ConnectionClosed,
                    TimeoutError,
                ):
                    continue
