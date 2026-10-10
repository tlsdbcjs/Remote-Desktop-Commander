"""Ephemeral authenticated native-session carrier; never stores raw payloads."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from racp_domain.models import RACPError
from racp_protocol.models import (
    NativeOpened,
    NativePacket,
    NativeStopped,
    NativeStopReason,
    NativeSubscribe,
    NativeUnsubscribe,
    new_id,
)
from racp_protocol.native_duplex import DuplexData, DuplexOpen, DuplexScope, parse_duplex_frame

if TYPE_CHECKING:
    from racp_gateway.service import Connection, ControlPlane


@dataclass
class NativeRelay:
    scope: DuplexScope
    connection: "Connection"
    max_bytes: int
    expires: float
    stream_id: str = ""
    ready: bool = False
    ended: bool = False
    transferred: int = 0
    queue: asyncio.Queue[NativeOpened | NativePacket | NativeStopped] = field(
        default_factory=lambda: asyncio.Queue(65)
    )

    def identity(self) -> dict[str, Any]:
        return {
            "device_id": self.scope.device_id,
            "agent_boot_id": self.scope.agent_boot_id,
            "connection_epoch": self.scope.connection_epoch,
            "stream_id": self.stream_id,
            "handle_id": self.scope.session_id,
        }


class NativeCarrierBroker:
    def __init__(self, control: "ControlPlane") -> None:
        self.control = control
        self.relays: dict[str, NativeRelay] = {}

    def register(self, value: dict[str, Any], connection: "Connection", owner: str) -> None:
        for key, item in list(self.relays.items()):
            if not item.stream_id and time.monotonic() >= item.expires:
                self.relays.pop(key)
        scope = DuplexScope.model_validate(value["native_scope"])
        handle = value["handle"]
        if (
            scope.principal_id != owner
            or scope.agent_boot_id != connection.boot_id
            or scope.connection_epoch != connection.epoch
            or scope.session_id != handle["id"]
            or handle.get("backend") not in {"racp-native-cdb", "racp-http-proxy-carrier"}
            or scope.device_id != handle["device_id"]
            or scope.workspace_id != handle["workspace_id"]
            or handle["owner"] != owner
            or handle["agent_boot_id"] != connection.boot_id
        ):
            raise RACPError("PERMISSION_DENIED", "Native preparation scope differs")
        previous = self.relays.get(scope.session_id)
        if previous:
            if previous.scope != scope or previous.connection is not connection:
                raise RACPError("CONFLICT", "Native session ID already bound")
            return
        budget, ttl = value["max_bytes"], value["lease_seconds"]
        if type(budget) is not int or not 16384 <= budget <= 64 * 1024**2:
            raise RACPError("INVALID_ARGUMENT", "Native byte limit invalid")
        if type(ttl) is not int or not 10 <= ttl <= 3600:
            raise RACPError("INVALID_ARGUMENT", "Native lease invalid")
        if len(self.relays) >= 32:
            raise RACPError("RESOURCE_EXHAUSTED", "Native carrier limit reached")
        self.relays[scope.session_id] = NativeRelay(
            scope, connection, budget, time.monotonic() + ttl
        )

    def check(self, relay: NativeRelay) -> None:
        device = self.control.store.device(relay.scope.device_id, relay.scope.principal_id)
        if device["revoked"]:
            raise RACPError("DEVICE_REVOKED", "Native device revoked")
        if relay.ended or time.monotonic() >= relay.expires:
            raise RACPError("HANDLE_EXPIRED", "Native session expired")
        current = self.control.connections.get(relay.scope.device_id)
        if current is not relay.connection or not current.ready:
            raise RACPError("STALE_CONNECTION", "Native connection fenced")
        if (current.boot_id, current.epoch) != (
            relay.scope.agent_boot_id,
            relay.scope.connection_epoch,
        ):
            raise RACPError("STALE_CONNECTION", "Native epoch fenced")

    async def open(self, device: str, handle: str, owner: str) -> NativeRelay:
        resource = self.control.handles.get(handle, owner)
        if resource["state"] != "ACTIVE":
            raise RACPError("HANDLE_EXPIRED", "Native resource is not active")
        relay = self.relays.get(handle)
        if (
            relay is None
            or resource["device_id"] != device
            or relay.scope.device_id != device
            or relay.scope.principal_id != owner
        ):
            raise RACPError("PERMISSION_DENIED", "Native stream owner or device differs")
        self.check(relay)
        if relay.stream_id:
            raise RACPError("RESOURCE_BUSY", "Native session already has a subscriber")
        relay.stream_id = new_id("native_stream")
        try:
            await relay.connection.send(
                NativeSubscribe(
                    **relay.identity(), principal_id=owner, workspace_id=relay.scope.workspace_id
                )
            )
        except BaseException:
            await self.close(relay, "error")
            raise
        return relay

    def account(self, relay: NativeRelay, packet: NativePacket) -> None:
        frame = parse_duplex_frame(packet.frame)
        if frame.scope_fingerprint != relay.scope.fingerprint:
            raise RACPError("PERMISSION_DENIED", "Native frame authority differs")
        if isinstance(frame, DuplexData):
            size = len(frame.decoded)
            if relay.transferred + size > relay.max_bytes:
                raise RACPError("RESOURCE_EXHAUSTED", "Native carrier byte limit")
            relay.transferred += size

    async def feed(
        self, message: NativeOpened | NativePacket | NativeStopped, connection: "Connection"
    ) -> None:
        relay = self.relays.get(message.handle_id)
        if relay is None:
            return
        if connection is not relay.connection or any(
            getattr(message, key) != value for key, value in relay.identity().items()
        ):
            raise RACPError("STALE_CONNECTION", "Native frame envelope fenced")
        if isinstance(message, NativeStopped):
            await self.close(relay, message.reason)
            return
        self.check(relay)
        if isinstance(message, NativeOpened):
            if relay.ready or DuplexScope.model_validate(message.scope) != relay.scope:
                raise RACPError("PERMISSION_DENIED", "Native ready scope differs")
            relay.ready = True
        else:
            if not relay.ready:
                raise RACPError("INVALID_ARGUMENT", "Native session not ready")
            self.account(relay, message)
        if relay.queue.qsize() >= 64:
            await self.close(relay, "overflow")
            raise RACPError("RESOURCE_EXHAUSTED", "Native consumer queue full")
        relay.queue.put_nowait(message)

    async def send(self, relay: NativeRelay, frame: dict[str, Any]) -> None:
        self.check(relay)
        if not relay.ready or (
            isinstance(parse_duplex_frame(frame), DuplexOpen)
            and relay.scope.endpoint_role != "agent_connector"
        ):
            raise RACPError("INVALID_ARGUMENT", "Host cannot create native channels")
        packet = NativePacket(**relay.identity(), frame=frame)
        self.account(relay, packet)
        await relay.connection.send(packet)

    async def close(self, relay: NativeRelay, reason: NativeStopReason = "closed") -> None:
        if relay.ended:
            return
        relay.ended = True
        self.relays.pop(relay.scope.session_id, None)
        while not relay.queue.empty():
            relay.queue.get_nowait()
        relay.queue.put_nowait(NativeStopped(**relay.identity(), reason=reason))
        if (
            relay.stream_id
            and self.control.connections.get(relay.scope.device_id) is relay.connection
        ):
            try:
                await asyncio.wait_for(
                    relay.connection.send(NativeUnsubscribe(**relay.identity())), 2
                )
            except (OSError, RACPError, TimeoutError):
                pass

    async def disconnected(self, device: str, connection: "Connection") -> None:
        for relay in list(self.relays.values()):
            if relay.scope.device_id == device and relay.connection is connection:
                await self.close(relay, "disconnected")
