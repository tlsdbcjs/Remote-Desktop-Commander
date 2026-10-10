import asyncio
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
from racp_domain.version import VERSION
from racp_observability.logging import log_event
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_policy.permissions import LocalPermissions, compile_permissions, legacy_permissions
from racp_protocol.artifacts import ARTIFACT_INPUT_OPERATIONS
from racp_protocol.clipboard import CLIPBOARD_MODELS
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
    NativeOpened,
    NativePacket,
    NativeStopped,
    NativeSubscribe,
    NativeUnsubscribe,
    OutcomeExpired,
    Reconcile,
    Request,
    ResourceHandle,
    Result,
    Welcome,
    decode_message,
    new_id,
)
from racp_protocol.native_operations import NATIVE_MODELS
from racp_protocol.os_observation import OS_OBSERVATION_MODELS, WINDOWS_INVENTORY_READS
from racp_protocol.provider_models import SENSITIVE_PROCESS_READS
from racp_protocol.proxy import PROXY_MODELS
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

from racp_agent.authorization import (
    WorkspaceBindings,
    authorize,
    contains_attachment,
    redact_arguments,
)
from racp_agent.browser_events import BrowserOutbox
from racp_agent.execution_identity import execution_identity
from racp_agent.native_runtime import ManagedNativeRuntime
from racp_agent.outputs import OutputSpool
from racp_agent.plugins.config import PluginConfig
from racp_agent.providers.browser import BrowserProvider
from racp_agent.providers.desktop import DesktopProvider
from racp_agent.providers.filesystem import FilesystemProvider
from racp_agent.providers.native_debugger import NativeDebuggerProvider
from racp_agent.providers.network_capture import NetworkCaptureProvider
from racp_agent.providers.os_observation import OSObservationProvider
from racp_agent.providers.process import ProcessProvider
from racp_agent.providers.process_dump import ProcessDumpProvider
from racp_agent.providers.proxy import ProxyProvider
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
        permissions: LocalPermissions | None = None,
        browser_allowed_origins: tuple[str, ...] = (),
        browser_cdp: bool = False,
        desktop_sessions: tuple[int, ...] = (),
        desktop_login_users: tuple[str, ...] = (),
        service_sid: str | None = None,
        plugins: PluginConfig | None = None,
        ca_file: Path | None = None,
        allowed_workspaces: tuple[WorkspaceSpec, ...] = (),
        native_runtime: ManagedNativeRuntime | None = None,
    ) -> None:
        require_secure_url(gateway)
        self.gateway = gateway.rstrip("/")
        self.ca_file, self.ssl = ca_file, tls_context(ca_file)
        self.credential, self.device_id = credential, device_id
        self.boot_id = new_id("boot")
        self.profile = profile
        self.permissions = compile_permissions(
            permissions
            if permissions is not None
            else legacy_permissions(desktop_enabled=bool(desktop_sessions or desktop_login_users))
        )
        self.journal = Journal(data_dir / "execution.db")
        self.journal.recover_agent()
        self.workspace_bindings = WorkspaceBindings(self.journal.db)
        self.provider = ShellProvider(workspace, data_dir / "spool", allowed_workspaces)
        self.filesystem = FilesystemProvider(workspace, data_dir / "spool", allowed_workspaces)
        self.os_observation = OSObservationProvider(workspace)
        self.network_capture = NetworkCaptureProvider(data_dir / "spool")
        self.process_dump = ProcessDumpProvider(data_dir / "spool")
        self.native = NativeDebuggerProvider(data_dir / "native", native_runtime, self.send,
            lambda: self.epoch, lambda: self.lease_expires)
        self.proxy = ProxyProvider(self.send,lambda:self.epoch,lambda:self.lease_expires)
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
            self.desktop.protected_pids()
            | self.reversing.protected_pids()
            | self.network_capture.protected_pids()
            | self.process_dump.protected_pids()
            | self.native.protected_pids()
        )
        self.native.extra_protected_pids = self.processes.extra_protected_pids
        self.proxy.protected = lambda: {os.getpid(),*self.processes.extra_protected_pids()}
        self.process_dump.extra_protected_pids = self.processes.extra_protected_pids
        self.reversing.protected = lambda: {
            os.getpid(),
            *[p.pid for p in psutil.Process().parents()],
            *self.desktop.protected_pids(),
            *self.reversing.protected_pids(),
            *self.network_capture.protected_pids(),
            *self.process_dump.protected_pids(),
            *self.native.protected_pids(),
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

    def effective_workspace(self, request: Request) -> str:
        workspace_id = request.context.workspace_id
        if request.operation.startswith("proxy.") and request.payload.get("handle_id"):
            proxy_session = self.proxy.sessions.get(request.payload["handle_id"])
            if proxy_session:
                if proxy_session.scope.principal_id != request.context.principal_id:
                    raise RACPError("PERMISSION_DENIED","Proxy belongs to another owner")
                workspace_id = proxy_session.context.workspace_id
        if request.operation.startswith("native.") and request.payload.get("handle_id"):
            native_session = self.native.sessions.get(request.payload["handle_id"])
            if native_session is not None:
                if native_session.scope.principal_id != request.context.principal_id:
                    raise RACPError("PERMISSION_DENIED", "Native session belongs to another owner")
                workspace_id = native_session.context.workspace_id
        if request.operation.startswith("terminal.") and request.payload.get("handle_id"):
            session = self.terminals.sessions.get(request.payload["handle_id"])
            if session is not None:
                if session.context.principal_id != request.context.principal_id:
                    raise RACPError(
                        "PERMISSION_DENIED", "Terminal belongs to another principal", layer="agent"
                    )
                workspace_id = session.context.workspace_id
        return workspace_id

    def workspace_fingerprint(self, workspace_id: str) -> str:
        guard = self.filesystem.guard.get(workspace_id)
        with guard.directory(guard.root) as parent:
            info = os.fstat(parent.fd) if parent.fd is not None else guard.root.stat()
            return canonical_digest(
                {
                    "path": os.path.normcase(str(guard.root)),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "birth_ns": getattr(info, "st_birthtime_ns", None),
                }
            )

    def bind_workspace(self, request: Request) -> None:
        workspace_id = self.effective_workspace(request)
        ids = {workspace_id, request.payload.get("destination_workspace_id") or workspace_id}
        self.workspace_bindings.bind(
            request, workspace_id, {id: self.workspace_fingerprint(id) for id in ids}
        )

    def delivery_workspace(self, request: Request) -> str:
        binding = self.workspace_bindings.resolve(request)
        try:
            if any(
                self.workspace_fingerprint(id) != fingerprint for id, fingerprint in binding.roots
            ):
                raise RACPError(
                    "PERMISSION_DENIED", "Retained output workspace root changed", layer="agent"
                )
        except (OSError, RACPError):
            raise RACPError(
                "PERMISSION_DENIED",
                "Retained output scope is no longer approved",
                layer="agent",
                reason="OUTPUT_SCOPE_CHANGED",
            ) from None
        return binding.workspace_id

    def authorize(
        self, request: Request, *, completed: bool = False, workspace_id: str | None = None
    ) -> None:
        if any(
            evaluate(request.operation, profile_rules(profile)) == Decision.DENY
            for profile in (self.profile, request.context.execution_profile_id)
        ) or (
            request.context.execution_profile_id == "trusted_personal"
            and self.profile != "trusted_personal"
            and REGISTRY[request.operation].side_effect
        ):
            raise RACPError(
                "PERMISSION_DENIED",
                "Agent local profile blocks this operation",
                layer="agent",
                execution_state="completed" if completed else "not_started",
            )
        workspace_id = workspace_id or self.effective_workspace(request)
        owned = request.operation == "process.terminate" and any(
            item.principal_id == request.context.principal_id
            and item.boot_id == self.boot_id
            and item.pid == request.payload.get("pid")
            and item.create_time == request.payload.get("create_time")
            for item in self.processes.managed.values()
        )
        authorize(
            self.permissions, request, owned=owned, completed=completed, workspace_id=workspace_id
        )

    def authorize_export(self, operation_id: str, path: Path) -> None:
        request = Request.model_validate(self.journal.get(operation_id)["request"])
        self.authorize(request, completed=True, workspace_id=self.delivery_workspace(request))
        if self.permissions.leaf("artifacts.export") != Decision.ALLOW:
            raise RACPError(
                "PERMISSION_DENIED",
                "Client blocks Artifact export",
                layer="agent",
                execution_state="completed",
            )
        if path.stat().st_size > self.permissions.constraints.max_output_bytes:
            raise RACPError(
                "PERMISSION_DENIED",
                "Artifact exceeds Client output ceiling",
                layer="agent",
                execution_state="completed",
            )

    def authorize_stream(self, request: StreamSubscribe) -> None:
        session = self.terminals.sessions.get(request.handle_id)
        workspace_id = (
            session.context.workspace_id if session is not None else request.context.workspace_id
        )
        decision = self.permissions.decision(
            "terminal.read",
            {"max_bytes": request.max_bytes},
            workspace_id=workspace_id,
        )
        if decision != Decision.ALLOW:
            raise RACPError("PERMISSION_DENIED", "Client blocks terminal stream", layer="agent")

    async def send(self, message: Any) -> None:
        if self.writer is None:
            raise ConnectionError("agent is disconnected")

        def prepare() -> str:
            if isinstance(message, (NativeOpened, NativePacket)):
                if message.handle_id in self.proxy.sessions:
                    self.proxy.guard_message(message)
                else:
                    self.native.guard_message(message)
            if isinstance(message, Result):
                return self.result_message(self.journal.get(message.operation_id)).model_dump_json()
            if isinstance(message, Reconcile) and message.records:
                records: list[Result] = []
                expired = list(message.expired)
                for original in message.records:
                    current = self.result_message(self.journal.get(original.operation_id))
                    if isinstance(current, Result):
                        records.append(current)
                    else:
                        expired.append(current)
                return message.model_copy(
                    update={"records": records, "expired": expired}
                ).model_dump_json()
            if message.type in {"stream_data", "stream_opened", "stream_gap"}:
                subscription = (
                    self.streams.subscriptions.get(message.stream_id) if self.streams else None
                )
                if subscription is None or subscription.request.handle_id != message.handle_id:
                    raise RACPError(
                        "PERMISSION_DENIED",
                        "Terminal subscription is no longer authorized",
                        layer="agent",
                    )
                self.authorize_stream(subscription.request)
            return str(message.model_dump_json())

        await self.writer.send(message, prepare=prepare)

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
        result, error = record["result"], record["error"]
        outputs = self.outputs.descriptors(record["id"])
        try:
            parsed = Request.model_validate(request)
            protected = (
                result is not None
                or bool(outputs)
                or contains_attachment(error)
                or bool((error or {}).get("details", {}).get("partial_result"))
            )
            self.authorize(
                parsed,
                completed=True,
                workspace_id=self.delivery_workspace(parsed) if protected else None,
            )
            if (
                request["operation"].startswith("process.")
                and self.permissions.leaf("process.arguments.read") != Decision.ALLOW
            ):
                result, error = redact_arguments(result), redact_arguments(error)
            if (outputs or contains_attachment(result) or contains_attachment(error)) and (
                self.permissions.leaf("artifacts.export") != Decision.ALLOW
            ):
                raise RACPError(
                    "PERMISSION_DENIED", "Client blocks retained Artifact delivery", layer="agent"
                )
            if any(
                item.size_bytes > self.permissions.constraints.max_output_bytes for item in outputs
            ) or (
                len(json.dumps({"result": result, "error": error}, ensure_ascii=False).encode())
                > self.permissions.constraints.max_output_bytes
            ):
                raise RACPError(
                    "PERMISSION_DENIED", "Retained output exceeds Client ceiling", layer="agent"
                )
        except RACPError as exc:
            # Keep both the journal and the original execution state immutable.
            # Delivery denial is explicit; it must never resend the old bytes.
            denied = asdict(exc.error)
            denied["execution_state"] = (
                "completed"
                if record["state"] == "SUCCEEDED"
                else ((error or {}).get("execution_state", "unknown"))
            )
            denied["details"].update(
                recorded_state=record["state"],
                output_authorization="withheld",
                permission_revision=self.permissions.revision,
            )
            result, error, outputs = None, denied, []
        return Result(
            device_id=self.device_id,
            agent_boot_id=self.boot_id,
            connection_epoch=self.epoch,
            request_id=request["request_id"],
            operation_id=record["id"],
            trace_id=request["trace_id"],
            state=record["state"],
            result=result,
            error=error,
            outputs=outputs,
        )

    async def _upload(self, path: Path, operation_id: str, media_type: str) -> str:
        self.authorize_export(operation_id, path)
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
                authorization_gate=lambda: self.authorize_export(operation_id, path),
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
            self.authorize(request)
            self.bind_workspace(request)
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
            self.authorize(request)
            if request.operation in {*NATIVE_MODELS,*PROXY_MODELS}:
                native_revision = self.permissions.revision

                def native_gate() -> None:
                    if self.permissions.revision != native_revision:
                        raise RACPError(
                            "PERMISSION_DENIED", "Native permission revision retired", layer="agent"
                        )
                    self.authorize(request)

                transport = self.proxy if request.operation in PROXY_MODELS else self.native
                outcome = await transport.execute(request.operation,payload,context,
                    native_revision,native_gate)
            elif request.operation == "network.capture":
                outcome = await self.network_capture.execute(
                    payload, context, lambda: self.authorize(request)
                )
            elif request.operation == "process.dump":
                outcome = await self.process_dump.execute(
                    payload, context, lambda: self.authorize(request)
                )
            elif request.operation in OS_OBSERVATION_MODELS:
                outcome = await self.os_observation.execute(request.operation, payload, context)
            elif request.operation.startswith("filesystem."):
                captured_revision = self.permissions.revision

                def file_gate() -> None:
                    if self.permissions.revision != captured_revision:
                        raise RACPError(
                            "PERMISSION_DENIED", "File operation permission revision retired",
                            layer="agent",
                        )
                    self.authorize(request, workspace_id=context.workspace_id)

                outcome = await self.filesystem.execute(
                    request.operation, payload, context, gate=file_gate
                )
            elif request.operation.startswith("terminal."):
                outcome = await self.terminals.execute(request.operation, payload, context)
            elif request.operation.startswith("process."):
                outcome = await self.processes.execute(request.operation, request.payload, context)
            elif request.operation.startswith("browser."):
                outcome = await self.browsers.execute(request.operation, payload, context)
            elif request.operation.startswith("desktop.") or request.operation in CLIPBOARD_MODELS:
                outcome = await self.desktop.execute(request.operation, payload, context)
            elif request.operation.startswith(("re.", "debugger.")):
                outcome = await self.reversing.execute(request.operation, payload, context)
            else:
                outcome = await self.provider.execute(request.operation, request.payload, context)
            self.authorize(request, completed=True)
            if (
                request.operation.startswith("process.")
                and self.permissions.leaf("process.arguments.read") != Decision.ALLOW
            ):
                outcome = redact_arguments(outcome)
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
            if result and len(json.dumps(result, ensure_ascii=False).encode()) > (
                self.permissions.constraints.max_output_bytes
            ):
                raise RACPError(
                    "PERMISSION_DENIED",
                    "Result exceeds Client output ceiling",
                    layer="agent",
                    execution_state="completed",
                )
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
                    self.authorize_export(request.operation_id, path)
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
                self.authorize_export(request.operation_id, preview_path)
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
            self.authorize(request, completed=True)
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
            if request.operation == "network.capture":
                self.network_capture.release(
                    request.operation_id,
                    preserve=bool(self.outputs.descriptors(request.operation_id)),
                )
            if request.operation == "process.dump":
                self.process_dump.release(
                    request.operation_id,
                    preserve=bool(self.outputs.descriptors(request.operation_id)),
                )
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
        request = request.model_copy(
            update={"payload": validate_payload(request.operation, request.payload)}
        )
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
                    capabilities=[self.desktop.capability(), self.desktop.clipboard_capability(),
                                  *self.reversing.capabilities()],
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
        handles.extend(handle.model_dump() for handle in self.native.handles())
        handles.extend(handle.model_dump() for handle in self.proxy.handles())
        handles.extend(
            item.handle(self.device_id, self.processes.instance_id)
            for item in list(self.processes.managed.values())
        )
        handles.sort(key=lambda handle: handle["state"] not in {"ACTIVE", "CREATING", "CLOSING"})
        return [ResourceHandle.model_validate(handle) for handle in handles[:128]]

    def compact_outcomes(self) -> None:
        self.journal.compact(pinned_operations=set(self.tasks) | self.outputs.pinned_operations())
        self.workspace_bindings.collect()

    async def lease_watchdog(self) -> None:
        last_maintenance = time.monotonic()
        while not self.stopping.is_set():
            await asyncio.sleep(0.25)
            if time.monotonic() - last_maintenance >= 60:
                self.compact_outcomes()
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
                await self.native.shutdown()
                await self.proxy.shutdown()
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
                        agent_version=VERSION,
                        supported_protocols=[1],
                        platform=platform.system(),
                        architecture=platform.machine(),
                        execution_identity=execution_identity(),
                        capabilities=[
                            self.native.capability(),
                            self.proxy.capability(),
                            self.network_capture.capability(),
                            self.process_dump.capability(),
                            self.desktop.clipboard_capability(),
                            Capability(
                                name="os_observation",
                                version="1.0.0",
                                operations=list(OS_OBSERVATION_MODELS),
                                attributes={
                                    "scope": "os_account",
                                    "network_routes": False,
                                    "network_dns_servers": False,
                                    "capture": False,
                                    "windows_inventory": os.name == "nt",
                                    "windows_only_operations": sorted(WINDOWS_INVENTORY_READS),
                                },
                            ),
                            Capability(
                                name="permissions",
                                version="1.0.0",
                                operations=[],
                                attributes={
                                    "source": "local",
                                    "revision": self.permissions.revision,
                                    "enforcement": "structured_api",
                                    "grants": dict(self.permissions.grants),
                                    "disabled_categories": sorted(
                                        self.permissions.disabled_categories
                                    ),
                                    "constraints": self.permissions.constraints.model_dump(
                                        mode="json"
                                    ),
                                    "local_approval_supported": False,
                                },
                            ),
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
                                    and name != "process.dump"
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
                    execution_identity=execution_identity(),
                    workspace=str(self.filesystem.guard.root),
                    profile=self.profile,
                )
                self.streams = TerminalStreams(
                    self.terminals,
                    self.send,
                    self.profile,
                    lambda: self.lease_expires,
                    self.authorize_stream,
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
                            elif isinstance(message, NativeSubscribe):
                                try:
                                    if message.handle_id in self.proxy.sessions:
                                        await self.proxy.subscribe(message)
                                    else:
                                        await self.native.subscribe(message)
                                except RACPError:
                                    await self.send(NativeStopped(
                                        **{key:getattr(message,key) for key in (
                                            "device_id","agent_boot_id","connection_epoch",
                                            "stream_id","handle_id"
                                        )},reason="error"
                                    ))
                            elif isinstance(message, (NativePacket,NativeUnsubscribe)):
                                if message.handle_id in self.proxy.sessions:
                                    await self.proxy.receive(message)
                                else:
                                    await self.native.receive(message)
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
                await self.native.shutdown()
                await self.proxy.shutdown()
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
