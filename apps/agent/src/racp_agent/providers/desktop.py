"""Session-explicit desktop Provider, using authenticated Broker IPC and verified PNG output."""

import asyncio
import base64
import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from racp_agent.broker.identity import process_identity
from racp_agent.broker.login_registration import LoginEndpoint, LoginGrant, LoginRegistrar
from racp_agent.broker.pipe import NativeError
from racp_agent.broker.supervisor import BrokerSupervisor
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.clipboard import CLIPBOARD_MODELS
from racp_protocol.desktop import DESKTOP_MODELS
from racp_protocol.models import Capability, timestamp


class DesktopProvider:
    def __init__(
        self,
        root: Path,
        spool: Path,
        device_id: str,
        sessions: tuple[int, ...] = (),
        login_users: tuple[str, ...] = (),
        service_sid: str | None = None,
    ) -> None:
        if (
            len(sessions) > 4
            or len(set(sessions)) != len(sessions)
            or any(not 1 <= s <= 0xFFFFFFFF for s in sessions)
        ):
            raise ValueError("desktop requires up to four distinct explicit Windows session IDs")
        self.spool = spool
        self.root, self.device_id = root, device_id
        if len(login_users) > 16 or len(set(login_users)) != len(login_users):
            raise ValueError("desktop requires at most 16 distinct login user SIDs")
        self.login_users, self.service_sid = login_users, service_sid
        self.registrar: LoginRegistrar | None = None
        self.registrar_task: asyncio.Task[None] | None = None
        self.login_error: str | None = None
        self.allow_registration: Callable[[], bool] = lambda: True
        self.endpoint_path = root / "login-endpoint.json"
        self.sessions = {s: BrokerSupervisor(root / str(s), s, device_id) for s in sessions}
        self.supported = os.name == "nt"

    def capability(self) -> Capability:
        states = [
            {
                "session_id": s,
                **b.status,
                "broker_running": b.process is not None and b.process.returncode is None,
            }
            for s, b in self.sessions.items()
        ]
        ready = self.supported and any(state["broker_running"] for state in states)
        guarded = any(
            state.get("input_guardian_available") and state.get("available") for state in states
        )
        return Capability(
            name="desktop",
            version="1.0.0",
            supported=self.supported,
            enabled=bool(self.sessions or self.login_users),
            healthy=ready,
            unavailable_reason="DESKTOP_NOT_CONFIGURED"
            if not self.sessions and not self.login_users
            else "SESSION_UNAVAILABLE"
            if not ready or not any(state["available"] for state in states)
            else None,
            operations=[name for name in DESKTOP_MODELS if name != "desktop.drag" or guarded],
            attributes={
                "sessions": states,
                "backend": "windows-local-session-broker",
                "health_scope": "broker_ipc",
                "user_input_detection_complete": False,
                "input_detection_backend": "low_level_hooks_with_self_input_tag",
                "input_lease_scope": "os_session",
                "drag_unavailable_reason": None if guarded else "INPUT_GUARDIAN_UNAVAILABLE",
                "structured_input_operations": ["desktop.invoke", "desktop.set_value"],
                "default_lease_ttl_ms": 15000,
                "observation_ttl_ms": 5000,
                "capture": "visible_rectangle",
                "service_cross_session_launch": False,
                "user_logon_registration": bool(self.login_users),
                "login_endpoint_file": str(self.endpoint_path) if self.login_users else None,
                "login_registration_error": self.login_error,
                "login_registration_available": self.registrar_task is not None
                and not self.registrar_task.done(),
            },
        )

    def clipboard_capability(self) -> Capability:
        desktop = self.capability()
        return Capability(
            name="clipboard", version="1.0.0", operations=list(CLIPBOARD_MODELS),
            supported=desktop.supported, enabled=desktop.enabled, healthy=desktop.healthy,
            unavailable_reason=desktop.unavailable_reason,
            attributes={"backend": "windows-local-session-broker", "format": "CF_UNICODETEXT",
                        "max_utf8_bytes": 8192, "compare_before_write": True,
                        "input_lease_required": False, "sessions": desktop.attributes["sessions"],
                        "rich_formats": False},
        )

    async def start(self) -> None:
        if self.login_users and self.supported:
            try:
                actor = process_identity(os.getpid())
                endpoint = LoginEndpoint(
                    device_id=self.device_id, agent_sid=actor.sid, service_sid=self.service_sid
                )
                self.registrar = LoginRegistrar(endpoint, self.login_users, self.adopt_login)
                endpoint.save(self.endpoint_path, self.login_users)
                self.registrar_task = asyncio.create_task(self.registrar.run())
            except (ValueError, PermissionError, RACPError, OSError, NativeError):
                if self.registrar is not None:
                    self.registrar.server.close()
                self.registrar = None
                self.login_error = "LOGIN_REGISTRATION_UNAVAILABLE"
        for broker in list(self.sessions.values()):
            try:
                async with broker.lock:
                    await broker.start()
            except (RACPError, OSError, NativeError, TimeoutError):
                broker.status = {"available": False, "reason": "Broker could not start"}

    async def adopt_login(self, grant: LoginGrant) -> None:
        if not self.allow_registration():
            raise RACPError(
                "SESSION_UNAVAILABLE", "Agent execution lease unavailable", layer="agent"
            )
        identifier = grant.config.session_id
        if identifier not in self.sessions and len(self.sessions) >= 4:
            raise RACPError("RESOURCE_EXHAUSTED", "desktop session limit", layer="agent")
        broker = self.sessions.get(identifier)
        if broker is None:
            broker = BrokerSupervisor(self.root / str(identifier), identifier, self.device_id)
            broker.login_managed = True
            self.sessions[identifier] = broker
        await broker.adopt(grant.config, grant.peer, grant.job, grant.commit)

    async def shutdown(self) -> None:
        if self.registrar_task is not None:
            self.registrar_task.cancel()
            await asyncio.gather(self.registrar_task, return_exceptions=True)
            self.registrar_task = None
        await self.cleanup()

    def protected_pids(self) -> set[int]:
        return set().union(*(broker.protected_pids() for broker in self.sessions.values()))

    async def cleanup(self) -> None:
        for broker in list(self.sessions.values()):
            async with broker.lock:
                await broker.abort()

    async def probe(self) -> None:
        for identifier, broker in list(self.sessions.items()):
            if broker.lock.locked():
                continue  # Observation/input work cannot delay the core heartbeat.
            try:
                async with broker.lock:
                    if broker.login_managed and (
                        broker.process is None or broker.process.returncode is not None
                    ):
                        await broker.stop()
                        self.sessions.pop(identifier, None)
                        continue
                    if broker.process is None or broker.process.returncode is not None:
                        await broker.start()
                    else:
                        broker.status = await broker.call(
                            "broker.status", {}, broker.private_context(), 1
                        )
                        if broker.login_managed and broker.status.get("input_guardian_available"):
                            await broker.discover_guard()
            except (RACPError, OSError, NativeError, TimeoutError):
                broker.status = {"available": False, "reason": "Broker health unavailable"}

    async def materialize(
        self,
        broker: BrokerSupervisor,
        descriptor: dict[str, Any],
        path: Path,
        context: dict[str, Any],
        deadline: float,
        max_bytes: int,
    ) -> None:
        size = descriptor.get("size_bytes")
        if (
            not isinstance(size, int)
            or not 0 < size <= max_bytes
            or descriptor.get("media_type") != "image/png"
        ):
            raise RACPError("EXECUTION_UNKNOWN", "invalid capture descriptor", layer="agent")
        partial = path.with_suffix(path.suffix + ".part")
        sha, offset = hashlib.sha256(), 0
        try:
            with partial.open("xb") as stream:
                while offset < size:
                    reply = await broker.rpc(
                        "broker.capture_read",
                        {
                            "capture_id": descriptor["capture_id"],
                            "offset": offset,
                            "length": min(32768, size - offset),
                        },
                        context,
                        deadline,
                    )
                    data = base64.b64decode(reply["data"], validate=True)
                    if (
                        not data
                        or len(data) > min(32768, size - offset)
                        or reply["offset"] != offset
                        or reply["next_offset"] != offset + len(data)
                    ):
                        raise RACPError(
                            "PRECONDITION_FAILED", "capture chunk offset mismatch", layer="agent"
                        )
                    if offset == 0 and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                        raise RACPError("PRECONDITION_FAILED", "capture is not PNG", layer="agent")
                    if stream.write(data) != len(data):
                        raise OSError("short capture spool write")
                    sha.update(data)
                    offset += len(data)
                    if bool(reply["eof"]) != (offset == size):
                        raise RACPError(
                            "PRECONDITION_FAILED", "capture chunk length mismatch", layer="agent"
                        )

                def sync() -> None:
                    stream.flush()
                    os.fsync(stream.fileno())

                flushing = asyncio.create_task(asyncio.to_thread(sync))
                try:
                    await asyncio.shield(flushing)
                except asyncio.CancelledError:
                    while not flushing.done():
                        try:
                            await asyncio.shield(flushing)
                        except asyncio.CancelledError:
                            continue
                    await flushing
                    raise
            if sha.hexdigest() != descriptor["sha256"]:
                raise RACPError("PRECONDITION_FAILED", "capture hash mismatch", layer="agent")
            os.replace(partial, path)
            try:
                await broker.rpc(
                    "broker.capture_release",
                    {"capture_id": descriptor["capture_id"]},
                    context,
                    deadline,
                )
            except (RACPError, OSError, NativeError, TimeoutError):
                pass  # Already verified bytes are retained; the Broker buffer has a fixed TTL.
        finally:
            partial.unlink(missing_ok=True)

    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        deadline = time.monotonic() + context.timeout_ms / 1000
        files: list[Path] = []
        try:
            if operation == "desktop.sessions":
                await self.probe()
                return {
                    "state": "SUCCEEDED",
                    "result": {"sessions": self.capability().attributes["sessions"]},
                    "error": None,
                }
            broker = self.sessions.get(payload["session_id"])
            if broker is None:
                raise RACPError(
                    "SESSION_UNAVAILABLE",
                    "session is not configured for this Device",
                    layer="agent",
                )
            ipc_context = {
                "owner_id": context.principal_id,
                "device_id": context.device_id,
                "operation_id": context.operation_id,
                "timeout_ms": min(30000, context.timeout_ms),
            }
            result = await broker.rpc(operation, payload, ipc_context, deadline)
            if operation in CLIPBOARD_MODELS:
                result.update(device_id=context.device_id, agent_boot_id=context.agent_boot_id,
                              observed_at=timestamp())
            if operation == "desktop.screenshot":
                original = self.spool / (context.operation_id + ".png")
                files.append(original)
                await self.materialize(
                    broker, result.pop("capture"), original, ipc_context, deadline, 32 * 1024 * 1024
                )
                result.update(
                    spool_path=str(original), artifact_media_type="image/png", artifact_id=None
                )
                if "preview" in result:
                    preview = self.spool / (context.operation_id + ".preview.png")
                    files.append(preview)
                    await self.materialize(
                        broker,
                        result["preview"].pop("capture"),
                        preview,
                        ipc_context,
                        deadline,
                        2 * 1024 * 1024,
                    )
                    result["preview"].update(
                        spool_path=str(preview), artifact_media_type="image/png", artifact_id=None
                    )
            return {"state": "SUCCEEDED", "result": result, "error": None}
        except RACPError as exc:
            for file in files:
                file.unlink(missing_ok=True)
            if exc.error.code in {"EXECUTION_UNKNOWN", "TIMEOUT"}:
                raise
            return {"state": "FAILED", "result": None, "error": asdict(exc.error)}
        except TimeoutError as exc:
            for file in files:
                file.unlink(missing_ok=True)
            raise RACPError(
                "TIMEOUT",
                "desktop execution budget elapsed",
                layer="agent",
                execution_state="unknown",
                cleanup_status=self.sessions[payload["session_id"]].cleanup_status,
            ) from exc
        except (OSError, NativeError) as exc:
            for file in files:
                file.unlink(missing_ok=True)
            raise RACPError(
                "EXECUTION_UNKNOWN",
                "desktop IPC outcome unavailable",
                layer="agent",
                execution_state="unknown",
            ) from exc
        except asyncio.CancelledError:
            for file in files:
                file.unlink(missing_ok=True)
            raise
