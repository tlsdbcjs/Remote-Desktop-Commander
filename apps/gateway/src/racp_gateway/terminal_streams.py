from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from racp_domain.models import RACPError
from racp_protocol.models import Connected, Context, new_id
from racp_protocol.streams import (
    STREAM_IN_FLIGHT_FRAMES,
    StreamAck,
    StreamAckInput,
    StreamData,
    StreamEnd,
    StreamGap,
    StreamOpened,
    StreamOpenInput,
    StreamSubscribe,
    StreamUnsubscribe,
)

if TYPE_CHECKING:
    from racp_gateway.service import Connection, ControlPlane


@dataclass
class Relay:
    request: StreamSubscribe
    owner: str
    connection: Connection
    queue: asyncio.Queue[Connected] = field(default_factory=lambda: asyncio.Queue(8))
    boundaries: deque[int] = field(default_factory=deque)
    delivered: set[int] = field(default_factory=set)
    consumed: int = 0
    received: int = 0
    opened: bool = False
    ended: bool = False
    drained: asyncio.Event = field(default_factory=asyncio.Event)

    def identity(self) -> dict[str, Any]:
        return {
            name: getattr(self.request, name)
            for name in (
                "device_id",
                "agent_boot_id",
                "connection_epoch",
                "stream_id",
                "handle_id",
            )
        }


class StreamBroker:
    def __init__(self, control: ControlPlane) -> None:
        self.control = control
        self.relays: dict[str, Relay] = {}

    async def open(self, device: str, handle: str, owner: str, input: StreamOpenInput) -> Relay:
        enrolled = self.control.store.device(device, owner)
        if enrolled["revoked"]:
            raise RACPError("DEVICE_REVOKED", "device was revoked")
        resource = self.control.handles.get(handle, owner)
        if resource["device_id"] != device or resource["type"] != "terminal":
            raise RACPError("PERMISSION_DENIED", "terminal stream scope differs")
        if resource["state"] in {"EXPIRED", "FAILED"}:
            raise RACPError("HANDLE_EXPIRED", "terminal expired")
        connection = self.control.connections.get(device)
        if not connection or not connection.ready:
            raise RACPError("DEVICE_OFFLINE", "device is offline")
        if resource["agent_boot_id"] != connection.boot_id:
            raise RACPError("HANDLE_EXPIRED", "terminal belongs to a previous Agent")
        if "terminal.read" not in connection.capabilities:
            raise RACPError("CAPABILITY_UNAVAILABLE", "terminal stream unavailable")
        if (
            len(self.relays) >= 64
            or sum(item.request.device_id == device for item in self.relays.values()) >= 16
        ):
            raise RACPError("RESOURCE_EXHAUSTED", "terminal stream limit reached")
        request = StreamSubscribe(
            device_id=device,
            agent_boot_id=connection.boot_id,
            connection_epoch=connection.epoch,
            stream_id=new_id("stream"),
            handle_id=handle,
            cursor=input.cursor,
            max_bytes=input.max_bytes,
            window_bytes=input.window_bytes,
            context=Context(
                principal_id=owner,
                execution_profile_id="read_only",
                policy_revision=1,
                workspace_id=resource.get("workspace_id") or "default",
            ),
        )
        relay = Relay(
            request, owner, connection, consumed=int(input.cursor), received=int(input.cursor)
        )
        self.relays[request.stream_id] = relay
        try:
            await connection.send(request)
        except BaseException:
            self.relays.pop(request.stream_id, None)
            cleanup = asyncio.create_task(connection.send(StreamUnsubscribe(**relay.identity())))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.gather(cleanup, return_exceptions=True)
            except (ConnectionError, RACPError):
                pass
            raise
        return relay

    def feed(
        self, message: StreamOpened | StreamData | StreamGap | StreamEnd, connection: Connection
    ) -> None:
        relay = self.relays.get(message.stream_id)
        if relay is None:
            return  # Late frame after consumer disconnect; no retained state is created.
        if relay.connection is not connection or any(
            getattr(message, name) != value for name, value in relay.identity().items()
        ):
            raise RACPError("STALE_CONNECTION", "stream frame is fenced")
        if relay.ended:
            return
        if relay.queue.qsize() >= 7:
            # Keep one slot reserved for the fenced/disconnected terminal frame.
            raise RACPError("RESOURCE_EXHAUSTED", "bounded stream relay queue full")
        if isinstance(message, StreamOpened):
            if (
                relay.opened
                or message.cursor != relay.request.cursor
                or message.window_bytes != relay.request.window_bytes
                or message.max_bytes != min(relay.request.max_bytes, relay.request.window_bytes)
            ):
                raise RACPError("INVALID_ARGUMENT", "stream opening differs")
            relay.opened = True
        elif isinstance(message, StreamData):
            if not relay.opened or int(message.byte_offset) != relay.received:
                raise RACPError("INVALID_ARGUMENT", "stream byte sequence differs")
            end = int(message.next_cursor)
            if (
                end - relay.consumed > relay.request.window_bytes
                or len(relay.boundaries) >= STREAM_IN_FLIGHT_FRAMES
                or end - int(message.byte_offset) > relay.request.max_bytes
            ):
                raise RACPError("RESOURCE_EXHAUSTED", "Agent exceeded stream credit")
            relay.received = end
            relay.boundaries.append(end)
        elif isinstance(message, StreamGap):
            if int(message.byte_offset) != relay.received or int(
                message.earliest_cursor
            ) - relay.received != int(message.lost_bytes):
                raise RACPError("INVALID_ARGUMENT", "stream gap differs")
        elif isinstance(message, StreamEnd):
            relay.ended = True
            if not relay.boundaries:
                relay.drained.set()
        try:
            relay.queue.put_nowait(message)
        except asyncio.QueueFull as exc:
            raise RACPError("RESOURCE_EXHAUSTED", "bounded stream relay queue full") from exc

    async def ack(self, relay: Relay, message: StreamAckInput) -> None:
        if message.stream_id != relay.request.stream_id:
            raise RACPError("PERMISSION_DENIED", "stream ACK belongs to another stream")
        offset = int(message.byte_offset)
        if offset <= relay.consumed:
            return
        if offset not in relay.delivered or offset not in relay.boundaries:
            raise RACPError("INVALID_ARGUMENT", "ACK must confirm a delivered chunk boundary")
        while relay.boundaries and relay.boundaries[0] <= offset:
            relay.delivered.discard(relay.boundaries.popleft())
        relay.consumed = offset
        if relay.ended and not relay.boundaries:
            relay.drained.set()
        if not relay.ended:
            await relay.connection.send(
                StreamAck(**relay.identity(), byte_offset=message.byte_offset)
            )

    async def close(self, relay: Relay) -> None:
        self.relays.pop(relay.request.stream_id, None)
        if (
            not relay.ended
            and self.control.connections.get(relay.request.device_id) is relay.connection
        ):
            try:
                await relay.connection.send(StreamUnsubscribe(**relay.identity()))
            except (ConnectionError, RACPError):
                pass

    def disconnected(
        self, device: str, connection: Connection, code: str = "DEVICE_OFFLINE"
    ) -> None:
        for relay in list(self.relays.values()):
            if (
                relay.request.device_id == device
                and relay.connection is connection
                and not relay.ended
            ):
                relay.ended = True
                if not relay.boundaries:
                    relay.drained.set()
                relay.queue.put_nowait(
                    StreamEnd(
                        **relay.identity(),
                        byte_offset=str(relay.received),
                        reason="error",
                        error=asdict(
                            RACPError(code, "terminal stream disconnected", layer="transport").error
                        ),
                    )
                )
