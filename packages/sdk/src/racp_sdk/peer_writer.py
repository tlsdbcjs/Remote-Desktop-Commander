import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import MAX_MESSAGE_BYTES


@dataclass
class Outgoing:
    text: str
    done: asyncio.Future[None]
    prepare: Callable[[], str] | None = None


class PeerWriter:
    """One transport writer, with separately bounded control and data capacity."""

    def __init__(self, send_text: Callable[[str], Awaitable[None]]) -> None:
        self.send_text = send_text
        self.control: asyncio.Queue[Outgoing] = asyncio.Queue(128)
        self.data: asyncio.Queue[Outgoing] = asyncio.Queue(8)
        self.wake = asyncio.Event()
        self.closed = False
        self.task: asyncio.Task[None] | None = None

    async def send(self, message: Any, *, prepare: Callable[[], str] | None = None) -> None:
        if self.closed:
            raise ConnectionError("peer writer is closed")
        text = message.model_dump_json()
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise RACPError("INVALID_ARGUMENT", "outgoing message exceeds 1 MiB")
        envelope = Outgoing(text, asyncio.get_running_loop().create_future(), prepare)
        queue = self.data if message.type == "stream_data" else self.control
        if self.task is None:
            self.task = asyncio.create_task(self.run())
        try:
            if queue is self.control:
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull as exc:
                    raise RACPError(
                        "RESOURCE_EXHAUSTED", "control queue full", retry_after_ms=100
                    ) from exc
            else:
                await queue.put(envelope)
            if self.closed:
                raise ConnectionError("peer writer is closed")
            self.wake.set()
            await envelope.done
        finally:
            if not envelope.done.done():
                envelope.done.cancel()

    async def run(self) -> None:
        current: Outgoing | None = None
        try:
            while True:
                self.wake.clear()
                try:
                    current = self.control.get_nowait()
                except asyncio.QueueEmpty:
                    try:
                        current = self.data.get_nowait()
                    except asyncio.QueueEmpty:
                        await self.wake.wait()
                        continue
                if not current.done.cancelled():
                    if current.prepare is not None:
                        try:
                            current.text = current.prepare()
                        except RACPError as exc:
                            current.done.set_exception(exc)
                            current = None
                            continue
                    await asyncio.wait_for(self.send_text(current.text), 5)
                    if not current.done.done():
                        current.done.set_result(None)
                current = None
        finally:
            self.closed = True
            if current is not None and not current.done.done():
                current.done.set_exception(ConnectionError("peer write interrupted"))
            for queue in (self.control, self.data):
                while not queue.empty():
                    entry = queue.get_nowait()
                    if not entry.done.done():
                        entry.done.set_exception(ConnectionError("peer writer stopped"))

    async def close(self) -> None:
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
