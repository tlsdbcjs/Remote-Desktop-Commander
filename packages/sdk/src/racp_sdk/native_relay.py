"""Multi-channel native transport core, awaiting authenticated carrier wiring.

Scope fingerprints only bind frames to authority supplied by the trusted caller;
they are not authentication. The carrier must authenticate that authority and
fixed connector/peer guards must verify owned OS endpoints before use.
"""

import asyncio
import base64
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.native_duplex import (
    DUPLEX_CHUNK_BYTES,
    DUPLEX_MAX_CHANNELS,
    DUPLEX_WINDOW_FRAMES,
    DuplexAck,
    DuplexData,
    DuplexEnd,
    DuplexOpen,
    DuplexScope,
    NativeFrame,
)


@dataclass
class _Channel:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    sent: int = 0
    received: int = 0
    acked: int = 0
    pending: deque[int] = field(default_factory=deque)
    credit: asyncio.Event = field(default_factory=asyncio.Event)
    inbound_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    outgoing_end: bool = False
    incoming_end: bool = False


class NativeDuplexRelay:
    def __init__(
        self,
        scope: DuplexScope,
        send: Callable[[NativeFrame], Awaitable[None]],
        *,
        gate: Callable[[], None],
        lease: Callable[[], float],
        connect: Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
        | None = None,
        max_bytes: int = 8 * 1024**2,
        timeout_seconds: float = 120,
    ) -> None:
        if not 1 <= max_bytes <= 64 * 1024**2 or not 0 < timeout_seconds <= 3600:
            raise ValueError("native relay budget exceeds bound")
        self.scope, self.send, self.gate, self.lease = scope, send, gate, lease
        self.connect = connect
        self.max_bytes = max_bytes
        self.deadline = time.monotonic() + timeout_seconds
        self.channels: dict[int, _Channel] = {}
        self.transferred_bytes = 0
        self.closed = asyncio.Event()
        self._closing = False
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._cleanup: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self._receive_open_lock = asyncio.Lock()
        self._next_channel = 1
        self._spawn(self._watch())

    @property
    def channel_count(self) -> int:
        return len(self.channels)

    @property
    def active_tasks(self) -> int:
        return sum(not task.done() for task in self._tasks)

    def _spawn(self, action: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(action)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _check(self) -> None:
        if self._closing:
            raise RACPError("HANDLE_EXPIRED", "Native session closed", layer="agent")
        self.gate()
        if time.monotonic() >= min(self.deadline, self.lease()):
            raise RACPError("HANDLE_EXPIRED", "Native session lease expired", layer="agent")

    def _charge(self, size: int) -> None:
        if self.transferred_bytes + size > self.max_bytes:
            raise RACPError("RESOURCE_EXHAUSTED", "Native byte budget exceeded", layer="agent")
        self.transferred_bytes += size

    async def _emit(self, frame: NativeFrame) -> None:
        async with self._send_lock:
            self._check()
            await asyncio.wait_for(self.send(frame), 5)
            self._check()

    async def listen(
        self, *, verify_peer: Callable[[asyncio.StreamWriter], None]
    ) -> tuple[str, int]:
        self._check()
        if self.connect is not None or self._server is not None:
            raise RACPError("CONFLICT", "Native role already configured")

        async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            channel = None
            try:
                self._check()
                if writer.get_extra_info("peername")[0] != "127.0.0.1":
                    raise PermissionError("native peer is not loopback")
                verify_peer(writer)
                if self._next_channel > DUPLEX_MAX_CHANNELS:
                    raise PermissionError("native connection limit")
                channel_id = self._next_channel
                self._next_channel += 1
                channel = _Channel(reader, writer)
                self.channels[channel_id] = channel
                await self._emit(
                    DuplexOpen(scope_fingerprint=self.scope.fingerprint, channel_id=channel_id)
                )
                self._spawn(self._read(channel_id, channel))
            except Exception:
                writer.close()
                if channel is not None:
                    await self.close()
            except asyncio.CancelledError:
                writer.close()
                raise

        self._server = await asyncio.start_server(
            lambda reader, writer: self._spawn(accept(reader, writer)), "127.0.0.1", 0
        )
        try:
            self._check()
        except BaseException:
            # Retirement can finish while start_server is still awaiting bind.
            # Its late result was not visible to the retirement cleanup task.
            self._server.close()
            await asyncio.wait_for(self._server.wait_closed(), 1)
            await self.close()
            raise
        address = self._server.sockets[0].getsockname()
        return str(address[0]), int(address[1])

    async def _read(self, channel_id: int, channel: _Channel) -> None:
        try:
            while True:
                self._check()
                while len(channel.pending) >= DUPLEX_WINDOW_FRAMES:
                    channel.credit.clear()
                    await channel.credit.wait()
                    self._check()
                raw = await channel.reader.read(DUPLEX_CHUNK_BYTES)
                self._check()
                if not raw:
                    channel.outgoing_end = True
                    await self._emit(
                        DuplexEnd(
                            scope_fingerprint=self.scope.fingerprint,
                            channel_id=channel_id,
                            byte_offset=channel.sent,
                        )
                    )
                    return
                offset = channel.sent
                self._charge(len(raw))
                channel.sent += len(raw)
                channel.pending.append(channel.sent)
                await self._emit(
                    DuplexData(
                        scope_fingerprint=self.scope.fingerprint,
                        channel_id=channel_id,
                        byte_offset=offset,
                        data_base64=base64.b64encode(raw).decode("ascii"),
                    )
                )
        except Exception:
            await self.close()

    async def receive(self, frame: NativeFrame) -> None:
        try:
            self._check()
            if frame.scope_fingerprint != self.scope.fingerprint:
                raise RACPError("PERMISSION_DENIED", "Native frame scope differs", layer="agent")
            if isinstance(frame, DuplexOpen):
                async with self._receive_open_lock:
                    self._check()
                    if self.connect is None or frame.channel_id != self._next_channel:
                        raise RACPError("INVALID_ARGUMENT", "Native open order differs")
                    reader, writer = await asyncio.wait_for(self.connect(), 5)
                    try:
                        self._check()
                    except BaseException:
                        writer.close()
                        raise
                    self._next_channel += 1
                    opened = _Channel(reader, writer)
                    self.channels[frame.channel_id] = opened
                    self._spawn(self._read(frame.channel_id, opened))
                return
            channel = self.channels.get(frame.channel_id)
            if channel is None:
                raise RACPError("INVALID_ARGUMENT", "Native channel not open")
            if isinstance(frame, DuplexAck):
                if frame.byte_offset <= channel.acked:
                    return
                if frame.byte_offset not in channel.pending:
                    raise RACPError("INVALID_ARGUMENT", "Native ACK is not a sent boundary")
                while channel.pending and channel.pending[0] <= frame.byte_offset:
                    channel.pending.popleft()
                channel.acked = frame.byte_offset
                channel.credit.set()
                return
            async with channel.inbound_lock:
                self._check()
                if channel.incoming_end or frame.byte_offset != channel.received:
                    raise RACPError("INVALID_ARGUMENT", "Native data order differs")
                if isinstance(frame, DuplexEnd):
                    channel.incoming_end = True
                    if channel.writer.can_write_eof():
                        channel.writer.write_eof()
                    else:
                        channel.writer.close()
                    return
                raw = frame.decoded
                self._charge(len(raw))
                self._check()
                channel.writer.write(raw)
                await asyncio.wait_for(channel.writer.drain(), 5)
                self._check()
                channel.received += len(raw)
                await self._emit(
                    DuplexAck(
                        scope_fingerprint=self.scope.fingerprint,
                        channel_id=frame.channel_id,
                        byte_offset=channel.received,
                    )
                )
        except asyncio.CancelledError:
            if not self._closing:
                await self.close()
            raise
        except BaseException:
            await self.close()
            raise

    async def _watch(self) -> None:
        try:
            while True:
                self._check()
                await asyncio.sleep(0.05)
        except Exception:
            await self.close()

    async def close(self) -> None:
        if self._closing and asyncio.current_task() in self._tasks:
            return
        if self._cleanup is None:
            self._closing = True
            origin = asyncio.current_task()
            self._cleanup = asyncio.create_task(self._finish_close(origin))
        interrupted = False
        while not self._cleanup.done():
            try:
                await asyncio.shield(self._cleanup)
            except asyncio.CancelledError:
                interrupted = True
        self._cleanup.result()
        current = asyncio.current_task()
        await asyncio.gather(
            *(task for task in self._tasks if task is not current), return_exceptions=True
        )
        if interrupted:
            raise asyncio.CancelledError

    async def _finish_close(self, origin: asyncio.Task[None] | None) -> None:
        try:
            if self._server is not None:
                self._server.close()
            for channel in self.channels.values():
                channel.writer.close()
            pending = [task for task in self._tasks if task is not origin]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if self._server is not None:
                await asyncio.wait_for(self._server.wait_closed(), 1)
            for channel in self.channels.values():
                try:
                    await asyncio.wait_for(channel.writer.wait_closed(), 1)
                except (OSError, TimeoutError):
                    pass
        finally:
            self.closed.set()
