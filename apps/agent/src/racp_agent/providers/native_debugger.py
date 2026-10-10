"""Managed CDB sessions carried by the Agent's existing outbound connection."""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import psutil
from racp_agent.native_duplex import NativeDuplexRelay
from racp_agent.native_runtime import ManagedNativeRuntime, PinnedNativeRuntime
from racp_agent.plugins.process import ContainedCommand, OwnedPluginProcess
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import (
    Capability,
    NativeOpened,
    NativePacket,
    NativeStopped,
    NativeStopReason,
    NativeSubscribe,
    NativeUnsubscribe,
    ResourceHandle,
    new_id,
    timestamp,
)
from racp_protocol.native_duplex import DuplexScope, NativeFrame, parse_duplex_frame
from racp_protocol.native_operations import NativePrepare, NativeTarget


@dataclass
class NativeSession:
    scope: DuplexScope
    context: ExecutionContext
    target: NativePrepare
    pinned: PinnedNativeRuntime
    pins: ExitStack
    gate: Callable[[], None]
    expires: float
    created: str = field(default_factory=timestamp)
    stream_id: str = ""
    state: Literal["ACTIVE", "CLOSING", "CLOSED", "FAILED"] = "ACTIVE"
    relay: NativeDuplexRelay | None = None
    address: tuple[str, int] | None = None
    owned: OwnedPluginProcess | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    revision: int = 1
    cleanup: asyncio.Task[None] | None = None


class NativeDebuggerProvider:
    def __init__(
        self,
        root: Path,
        runtime: ManagedNativeRuntime | None,
        send: Callable[[Any], Awaitable[None]],
        epoch: Callable[[], int],
        lease: Callable[[], float],
    ) -> None:
        self.root, self.runtime, self.send, self.epoch, self.lease = (
            root,
            runtime,
            send,
            epoch,
            lease,
        )
        self.instance = new_id("native_provider")
        self.sessions: dict[str, NativeSession] = {}
        self.extra_protected_pids: Callable[[], set[int]] = lambda: set()

    def capability(self) -> Capability:
        available = os.name == "nt" and self.runtime is not None
        return Capability(
            name="native",
            version="1.0.0",
            operations=["native.prepare", "native.start", "native.close"],
            installed=self.runtime is not None,
            supported=os.name == "nt",
            enabled=available,
            healthy=available,
            unavailable_reason=None if available else "managed_cdb_runtime_missing",
            attributes={
                "opaque_native_commands": True,
                "broad_execution_grant_required": True,
                "manual_analysis_ide_required": False,
                "max_channels": 16,
            },
        )

    def protected_pids(self) -> set[int]:
        result = set()
        if os.name == "nt":
            import win32job

            for session in self.sessions.values():
                if session.owned and session.owned.job:
                    result.update(
                        win32job.QueryInformationJobObject(
                            session.owned.job, win32job.JobObjectBasicProcessIdList
                        )
                    )
        return result

    def handle(self, session: NativeSession) -> ResourceHandle:
        return ResourceHandle(
            id=session.scope.session_id,
            type="debugger",
            device_id=session.scope.device_id,
            owner=session.scope.principal_id,
            agent_boot_id=session.scope.agent_boot_id,
            workspace_id=session.scope.workspace_id,
            provider_instance_id=self.instance,
            resource_revision=str(session.revision),
            created_at=session.created,
            last_access_at=session.created,
            expires_at=(
                datetime.now(UTC) + timedelta(seconds=max(0, session.expires - time.monotonic()))
            ).isoformat(),
            state=session.state,
            availability="available" if session.state == "ACTIVE" else "unavailable",
            pid=session.target.pid,
            create_time=session.target.create_time,
            ownership="borrowed",
            backend="racp-native-cdb",
            backend_version=session.pinned.version,
        )

    def handles(self) -> list[ResourceHandle]:
        return [self.handle(session) for session in self.sessions.values()]

    def identity(self, handle: str, context: ExecutionContext) -> NativeSession:
        session = self.sessions.get(handle)
        if session is None or session.state != "ACTIVE":
            raise RACPError("HANDLE_EXPIRED", "Native session unavailable", layer="agent")
        if (session.scope.principal_id, session.scope.device_id, session.scope.agent_boot_id) != (
            context.principal_id,
            context.device_id,
            context.agent_boot_id,
        ):
            raise RACPError("PERMISSION_DENIED", "Native session ownership differs", layer="agent")
        self.check(session)
        return session

    def check(self, session: NativeSession) -> None:
        session.gate()
        if session.state != "ACTIVE" or time.monotonic() >= min(session.expires, self.lease()):
            raise RACPError("HANDLE_EXPIRED", "Native execution lease expired", layer="agent")
        if session.scope.connection_epoch != self.epoch():
            raise RACPError("STALE_CONNECTION", "Native epoch changed", layer="agent")

    def target_check(self, target: NativePrepare) -> None:
        protected = {
            os.getpid(),
            *[p.pid for p in psutil.Process().parents()],
            *self.extra_protected_pids(),
            *self.protected_pids(),
        }
        if target.pid in protected:
            raise RACPError("PERMISSION_DENIED", "Protected native target", layer="agent")
        try:
            current = psutil.Process(target.pid)
            if abs(current.create_time() - target.create_time) > 1e-6:
                raise RACPError(
                    "PRECONDITION_FAILED", "Native target identity changed", layer="agent"
                )
        except psutil.NoSuchProcess:
            raise RACPError("PROCESS_NOT_FOUND", "Native target exited", layer="agent") from None
        except psutil.AccessDenied:
            raise RACPError(
                "PERMISSION_DENIED", "Native target access denied", layer="agent"
            ) from None

    async def prepare(
        self,
        payload: dict[str, Any],
        context: ExecutionContext,
        revision: str,
        gate: Callable[[], None],
    ) -> dict[str, Any]:
        if self.runtime is None or os.name != "nt":
            raise RACPError("CAPABILITY_UNAVAILABLE", "Managed CDB runtime unavailable")
        for key in [key for key,value in self.sessions.items() if value.state == "CLOSED"]:
            if len(self.sessions) < 32:
                break
            self.sessions.pop(key)
        if sum(s.state == "ACTIVE" for s in self.sessions.values()) >= 4:
            raise RACPError("RESOURCE_EXHAUSTED", "Native session limit")
        target = NativePrepare.model_validate(payload)
        if target.agent_boot_id != context.agent_boot_id:
            raise RACPError("PRECONDITION_FAILED", "Native Agent boot changed")
        gate()
        self.target_check(target)
        pins = ExitStack()
        try:
            pinned = pins.enter_context(self.runtime.pin())
            import win32api

            pins.callback(win32api.OpenProcess(0x101000, False, target.pid).Close)
            gate()
            self.target_check(target)
            scope = DuplexScope(
                session_id=new_id("native"),
                device_id=context.device_id,
                agent_boot_id=context.agent_boot_id,
                connection_epoch=self.epoch(),
                principal_id=context.principal_id,
                workspace_id=context.workspace_id,
                permission_revision=revision,
            )
            session = NativeSession(
                scope, context, target, pinned, pins, gate, time.monotonic() + target.lease_seconds
            )
            self.sessions[scope.session_id] = session
            session.tasks.append(asyncio.create_task(self.watch(session)))
            return {
                "state": "SUCCEEDED",
                "result": {
                    "handle": self.handle(session).model_dump(),
                    "native_scope": scope.model_dump(),
                    "max_bytes": target.max_bytes,
                    "lease_seconds": target.lease_seconds,
                    "symbol_cache": str(self.root / scope.session_id / "symbols"),
                },
            }
        except BaseException:
            pins.close()
            raise

    async def subscribe(self, message: NativeSubscribe) -> None:
        session = self.sessions.get(message.handle_id)
        if session is None:
            raise RACPError("HANDLE_EXPIRED", "Native session not prepared")
        expected = (
            session.scope.device_id,
            session.scope.agent_boot_id,
            session.scope.connection_epoch,
            session.scope.principal_id,
            session.scope.workspace_id,
        )
        if expected != (
            message.device_id,
            message.agent_boot_id,
            message.connection_epoch,
            message.principal_id,
            message.workspace_id,
        ):
            raise RACPError("PERMISSION_DENIED", "Native subscriber scope differs")
        self.check(session)
        if session.stream_id:
            raise RACPError("RESOURCE_BUSY", "Native session already subscribed")
        session.stream_id = message.stream_id
        await self.send(NativeOpened(**self.envelope(session), scope=session.scope.model_dump()))

    def envelope(self, session: NativeSession) -> dict[str, Any]:
        return {
            "device_id": session.scope.device_id,
            "agent_boot_id": session.scope.agent_boot_id,
            "connection_epoch": session.scope.connection_epoch,
            "handle_id": session.scope.session_id,
            "stream_id": session.stream_id,
        }

    async def start(self, session: NativeSession) -> dict[str, Any]:
        self.check(session)
        self.target_check(session.target)
        if not session.stream_id or session.relay is not None:
            raise RACPError("PRECONDITION_FAILED", "Native carrier absent or already started")
        working = self.root / session.scope.session_id
        working.mkdir(parents=True, exist_ok=False)

        async def send(frame: NativeFrame) -> None:
            self.check(session)
            await self.send(NativePacket(**self.envelope(session), frame=frame.model_dump()))

        def verify(writer: asyncio.StreamWriter) -> None:
            owned = session.owned
            if owned is None or owned.job is None:
                raise PermissionError("native helper not active")
            import win32job

            members = set(
                win32job.QueryInformationJobObject(owned.job, win32job.JobObjectBasicProcessIdList)
            )
            local, peer = writer.get_extra_info("sockname"), writer.get_extra_info("peername")
            found = [
                row.pid
                for row in psutil.net_connections(kind="tcp")
                if row.laddr
                and row.raddr
                and tuple(row.laddr) == tuple(peer)
                and tuple(row.raddr) == tuple(local)
                and row.status == psutil.CONN_ESTABLISHED
            ]
            if len(found) != 1 or found[0] not in members:
                raise PermissionError("native helper peer differs")
            if os.path.normcase(psutil.Process(found[0]).exe()) != os.path.normcase(
                str(session.pinned.executable)
            ):
                raise PermissionError("native helper executable differs")

        session.relay = NativeDuplexRelay(
            session.scope,
            send,
            gate=lambda: self.check(session),
            lease=lambda: min(session.expires, self.lease()),
            max_bytes=session.target.max_bytes,
            timeout_seconds=session.target.lease_seconds,
        )
        try:
            session.address = await session.relay.listen(verify_peer=verify)
            self.check(session)
            self.target_check(session.target)
            session.owned = await OwnedPluginProcess.start(
                ContainedCommand(
                    session.pinned.reverse_command(session.target.pid, session.address[1], working),
                    str(working),
                    max_memory_bytes=512 * 1024**2,
                )
            )

            async def discard(stream: asyncio.StreamReader | None) -> None:
                assert stream is not None
                total = 0
                while chunk := await stream.read(4096):
                    total += len(chunk)
                    if total > 1024**2:
                        await self.close(session, "error")
                        return

            session.tasks.extend(
                [
                    asyncio.create_task(discard(session.owned.process.stdout)),
                    asyncio.create_task(discard(session.owned.process.stderr)),
                ]
            )
            return {"state": "SUCCEEDED", "result": {"handle": self.handle(session).model_dump()}}
        except BaseException:
            await self.close(session, "error")
            raise

    async def receive(self, message: NativePacket | NativeUnsubscribe) -> None:
        session = self.sessions.get(message.handle_id)
        if session is None:
            return
        if any(getattr(message, key) != value for key, value in self.envelope(session).items()):
            raise RACPError("STALE_CONNECTION", "Native envelope differs")
        if isinstance(message, NativeUnsubscribe):
            await self.close(session)
            return
        self.check(session)
        if session.relay is None:
            raise RACPError("PRECONDITION_FAILED", "Native helper not started")
        await session.relay.receive(parse_duplex_frame(message.frame))

    async def watch(self, session: NativeSession) -> None:
        try:
            while True:
                self.check(session)
                if session.relay and session.relay.closed.is_set():
                    break
                if session.owned and session.owned.process.returncode is not None:
                    break
                await asyncio.sleep(0.05)
        except Exception:
            pass
        await self.close(session, "expired")

    async def close(self, session: NativeSession, reason: NativeStopReason = "closed") -> None:
        if session.cleanup is not None and asyncio.current_task() in session.tasks:
            return
        if session.cleanup is None:
            origin = asyncio.current_task()
            session.cleanup = asyncio.create_task(self._close(session, reason, origin))
        interrupted = False
        while not session.cleanup.done():
            try:
                await asyncio.shield(session.cleanup)
            except asyncio.CancelledError:
                interrupted = True
        session.cleanup.result()
        current = asyncio.current_task()
        await asyncio.gather(
            *(task for task in session.tasks if task is not current), return_exceptions=True
        )
        if interrupted:
            raise asyncio.CancelledError

    async def _close(
        self, session: NativeSession, reason: NativeStopReason, origin: asyncio.Task[Any] | None
    ) -> None:
        session.state = "CLOSING"
        session.revision += 1
        if session.relay:
            await session.relay.close()
            session.relay = None
        if session.owned:
            await session.owned.stop()
            if session.owned.cleanup_status != "complete":
                session.state = "FAILED"
                raise RACPError(
                    "EXECUTION_UNKNOWN",
                    "Native helper cleanup unverified",
                    cleanup_status="unverified",
                    execution_state="unknown",
                )
        tasks = [task for task in session.tasks if task is not origin]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        session.pins.close()
        session.state = "CLOSED"
        session.revision += 1
        if session.stream_id:
            try:
                await self.send(NativeStopped(**self.envelope(session), reason=reason))
            except Exception:
                pass

    def guard_message(self, message: NativeOpened | NativePacket) -> None:
        session = self.sessions.get(message.handle_id)
        if session is None or any(
            getattr(message, key) != value for key, value in self.envelope(session).items()
        ):
            raise RACPError("PERMISSION_DENIED", "Native output scope differs", layer="agent")
        self.check(session)

    async def shutdown(self) -> None:
        for session in list(self.sessions.values()):
            await self.close(session, "disconnected")

    async def execute(
        self,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
        revision: str,
        gate: Callable[[], None],
    ) -> dict[str, Any]:
        if operation == "native.prepare":
            return await self.prepare(payload, context, revision, gate)
        target = NativeTarget.model_validate(payload)
        session = self.identity(target.handle_id, context)
        if operation == "native.start":
            return await self.start(session)
        await self.close(session)
        return {"state": "SUCCEEDED", "result": {"handle": self.handle(session).model_dump()}}
