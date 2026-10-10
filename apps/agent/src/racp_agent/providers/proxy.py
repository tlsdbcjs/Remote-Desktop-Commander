"""Process-bound bidirectional carrier for original host proxy/replay tools.

No OS proxy settings, root certificates or third-party applications are changed.
Transparent native bytes require explicit broad execution/intercept/replay grants.
"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psutil
from racp_agent.tcp_peer import verify_tcp_peer
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
from racp_protocol.native_operations import NativeTarget
from racp_protocol.proxy import ProxyPrepare
from racp_sdk.native_relay import NativeDuplexRelay


@dataclass
class ProxySession:
    scope: DuplexScope
    context: ExecutionContext
    target: ProxyPrepare
    pins: ExitStack
    gate: Callable[[], None]
    expires: float
    created: str = field(default_factory=timestamp)
    stream_id: str = ""
    state: Literal["ACTIVE", "CLOSING", "CLOSED"] = "ACTIVE"
    relay: NativeDuplexRelay | None = None
    address: tuple[str, int] | None = None
    monitor: asyncio.Task[None] | None = None
    cleanup: asyncio.Task[None] | None = None
    revision: int = 1


class ProxyProvider:
    def __init__(
        self,
        send: Callable[[Any], Awaitable[None]],
        epoch: Callable[[], int],
        lease: Callable[[], float],
    ) -> None:
        self.send, self.epoch, self.lease = send, epoch, lease
        self.sessions: dict[str, ProxySession] = {}
        self.instance = new_id("proxy_provider")
        self.protected: Callable[[], set[int]] = lambda: {os.getpid()}

    def capability(self) -> Capability:
        available = os.name == "nt"
        return Capability(
            name="proxy",
            version="1.0.0",
            operations=["proxy.prepare", "proxy.close"],
            enabled=available,
            healthy=available,
            supported=available,
            unavailable_reason=None if available else "windows_peer_identity_required",
            attributes={
                "backend": "process_bound_tcp_carrier",
                "opaque_native_commands": True,
                "system_proxy_changes": False,
                "ca_installation": False,
                "max_channels": 16,
            },
        )

    def handle(self, s: ProxySession) -> ResourceHandle:
        return ResourceHandle(
            id=s.scope.session_id,
            type="interactive-process",
            device_id=s.scope.device_id,
            owner=s.scope.principal_id,
            agent_boot_id=s.scope.agent_boot_id,
            workspace_id=s.scope.workspace_id,
            provider_instance_id=self.instance,
            resource_revision=str(s.revision),
            created_at=s.created,
            last_access_at=s.created,
            expires_at=(
                datetime.now(UTC) + timedelta(seconds=max(0, s.expires - time.monotonic()))
            ).isoformat(),
            state=s.state,
            availability="available" if s.state == "ACTIVE" else "unavailable",
            pid=s.target.pid,
            create_time=s.target.create_time,
            ownership="borrowed",
            backend="racp-http-proxy-carrier",
        )

    def handles(self) -> list[ResourceHandle]:
        return [self.handle(s) for s in self.sessions.values()]

    def envelope(self, s: ProxySession) -> dict[str, Any]:
        return {
            "device_id": s.scope.device_id,
            "agent_boot_id": s.scope.agent_boot_id,
            "connection_epoch": s.scope.connection_epoch,
            "stream_id": s.stream_id,
            "handle_id": s.scope.session_id,
        }

    def check(self, s: ProxySession) -> None:
        s.gate()
        if s.state != "ACTIVE" or time.monotonic() >= min(s.expires, self.lease()):
            raise RACPError("HANDLE_EXPIRED", "Proxy session expired", layer="agent")
        if s.scope.connection_epoch != self.epoch():
            raise RACPError("STALE_CONNECTION", "Proxy epoch changed", layer="agent")
        try:
            if abs(psutil.Process(s.target.pid).create_time() - s.target.create_time) > 1e-6:
                raise RACPError("PRECONDITION_FAILED", "Proxy peer PID changed", layer="agent")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
            raise RACPError("PROCESS_NOT_FOUND", "Proxy peer unavailable", layer="agent") from error

    async def prepare(
        self,
        payload: dict[str, Any],
        context: ExecutionContext,
        revision: str,
        gate: Callable[[], None],
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise RACPError("CAPABILITY_UNAVAILABLE", "Windows peer binding required")
        target = ProxyPrepare.model_validate(payload)
        if target.agent_boot_id != context.agent_boot_id:
            raise RACPError("PRECONDITION_FAILED", "Proxy Agent boot changed")
        if target.pid in self.protected() or target.pid in {
            p.pid for p in psutil.Process().parents()
        }:
            raise RACPError("PERMISSION_DENIED", "Protected proxy peer")
        for key in [key for key, s in self.sessions.items() if s.state == "CLOSED"]:
            if len(self.sessions) < 32:
                break
            self.sessions.pop(key)
        if sum(s.state == "ACTIVE" for s in self.sessions.values()) >= 4:
            raise RACPError("RESOURCE_EXHAUSTED", "Proxy session limit")
        pins = ExitStack()
        session = None
        try:
            gate()
            import win32api

            pins.callback(win32api.OpenProcess(0x101000, False, target.pid).Close)
            scope = DuplexScope(
                session_id=new_id("proxy"),
                device_id=context.device_id,
                agent_boot_id=context.agent_boot_id,
                connection_epoch=self.epoch(),
                principal_id=context.principal_id,
                workspace_id=context.workspace_id,
                permission_revision=revision,
                endpoint_role=target.direction,
            )
            session = ProxySession(
                scope, context, target, pins, gate, time.monotonic() + target.lease_seconds
            )
            self.check(session)

            async def send(frame: NativeFrame) -> None:
                assert session is not None
                self.check(session)
                if not session.stream_id:
                    raise RACPError("PRECONDITION_FAILED", "Proxy carrier not subscribed")
                await self.send(NativePacket(**self.envelope(session), frame=frame.model_dump()))

            async def connector() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
                assert session is not None and target.local_port is not None
                self.check(session)
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", target.local_port), 5
                )
                try:
                    self.check(session)
                    verify_tcp_peer(writer, target.pid, target.create_time)
                except BaseException:
                    writer.close()
                    await writer.wait_closed()
                    raise
                return reader, writer

            session.relay = NativeDuplexRelay(
                scope,
                send,
                gate=lambda: self.check(session),
                lease=lambda: min(session.expires, self.lease()),
                max_bytes=target.max_bytes,
                timeout_seconds=target.lease_seconds,
                connect=connector if target.direction == "agent_connector" else None,
            )
            if target.direction == "agent_listener":
                session.address = await session.relay.listen(
                    verify_peer=lambda writer: verify_tcp_peer(
                        writer, target.pid, target.create_time
                    )
                )
            self.sessions[scope.session_id] = session
            session.monitor = asyncio.create_task(self.watch(session))
            return {
                "state": "SUCCEEDED",
                "result": {
                    "handle": self.handle(session).model_dump(),
                    "native_scope": scope.model_dump(),
                    "max_bytes": target.max_bytes,
                    "lease_seconds": target.lease_seconds,
                    "proxy_url": f"http://127.0.0.1:{session.address[1]}"
                    if session.address
                    else None,
                },
            }
        except BaseException:
            if session and session.relay:
                await session.relay.close()
            pins.close()
            raise

    async def subscribe(self, message: NativeSubscribe) -> None:
        s = self.sessions.get(message.handle_id)
        if s is None:
            raise RACPError("HANDLE_EXPIRED", "Proxy session unavailable")
        if (
            message.device_id,
            message.agent_boot_id,
            message.connection_epoch,
            message.principal_id,
            message.workspace_id,
        ) != (
            s.scope.device_id,
            s.scope.agent_boot_id,
            s.scope.connection_epoch,
            s.scope.principal_id,
            s.scope.workspace_id,
        ):
            raise RACPError("PERMISSION_DENIED", "Proxy subscriber differs")
        self.check(s)
        if s.stream_id:
            raise RACPError("RESOURCE_BUSY", "Proxy session subscribed")
        s.stream_id = message.stream_id
        await self.send(NativeOpened(**self.envelope(s), scope=s.scope.model_dump()))

    async def receive(self, message: NativePacket | NativeUnsubscribe) -> None:
        s = self.sessions.get(message.handle_id)
        if s is None:
            return
        if any(getattr(message, key) != value for key, value in self.envelope(s).items()):
            raise RACPError("STALE_CONNECTION", "Proxy envelope differs")
        if isinstance(message, NativeUnsubscribe):
            await self.close(s)
            return
        self.check(s)
        assert s.relay is not None
        await s.relay.receive(parse_duplex_frame(message.frame))

    def guard_message(self, message: NativePacket | NativeOpened) -> None:
        s = self.sessions.get(message.handle_id)
        if s is None or any(
            getattr(message, key) != value for key, value in self.envelope(s).items()
        ):
            raise RACPError("PERMISSION_DENIED", "Proxy output scope differs")
        self.check(s)

    async def watch(self, s: ProxySession) -> None:
        try:
            while True:
                self.check(s)
                if s.relay and s.relay.closed.is_set():
                    break
                await asyncio.sleep(0.05)
        except Exception:
            pass
        await self.close(s, "expired")

    async def close(self, s: ProxySession, reason: NativeStopReason = "closed") -> None:
        if s.cleanup is not None and asyncio.current_task() is s.monitor:
            return
        if s.cleanup is None:
            origin = asyncio.current_task()
            s.cleanup = asyncio.create_task(self._close(s, reason, origin))
        interrupted = False
        while not s.cleanup.done():
            try:
                await asyncio.shield(s.cleanup)
            except asyncio.CancelledError:
                interrupted = True
        s.cleanup.result()
        if s.monitor and s.monitor is not asyncio.current_task():
            await asyncio.gather(s.monitor, return_exceptions=True)
        if interrupted:
            raise asyncio.CancelledError

    async def _close(
        self, s: ProxySession, reason: NativeStopReason, origin: asyncio.Task[Any] | None
    ) -> None:
        s.state = "CLOSING"
        s.revision += 1
        if s.relay:
            await s.relay.close()
            s.relay = None
        if s.monitor is not None and s.monitor is not origin:
            s.monitor.cancel()
            await asyncio.gather(s.monitor, return_exceptions=True)
        s.pins.close()
        s.state = "CLOSED"
        s.revision += 1
        if s.stream_id:
            try:
                await self.send(NativeStopped(**self.envelope(s), reason=reason))
            except Exception:
                pass

    async def shutdown(self) -> None:
        for s in list(self.sessions.values()):
            await self.close(s, "disconnected")

    async def execute(
        self,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
        revision: str,
        gate: Callable[[], None],
    ) -> dict[str, Any]:
        if operation == "proxy.prepare":
            return await self.prepare(payload, context, revision, gate)
        target = NativeTarget.model_validate(payload)
        s = self.sessions.get(target.handle_id)
        if s is None or (
            s.scope.principal_id,
            s.scope.agent_boot_id,
            s.scope.device_id,
            s.scope.workspace_id,
        ) != (
            context.principal_id,
            context.agent_boot_id,
            context.device_id,
            context.workspace_id,
        ):
            raise RACPError("PERMISSION_DENIED", "Proxy session belongs to another context")
        gate()
        await self.close(s)
        return {"state": "SUCCEEDED", "result": {"handle": self.handle(s).model_dump()}}
