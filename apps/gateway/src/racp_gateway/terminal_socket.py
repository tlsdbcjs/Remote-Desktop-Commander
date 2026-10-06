import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from racp_domain.models import RACPError
from racp_protocol.models import MAX_MESSAGE_BYTES, validate_tree
from racp_protocol.streams import StreamAckInput, StreamData, StreamEnd, StreamOpenInput

if TYPE_CHECKING:
    from racp_gateway.service import ControlPlane


def input_json(raw: str) -> object:
    if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise RACPError("INVALID_ARGUMENT", "stream frame exceeds 1 MiB")
    value = json.loads(raw)
    validate_tree(value)
    return value


async def terminal_socket(
    socket: WebSocket,
    device: str,
    handle: str,
    owner: str,
    control: "ControlPlane",
    authenticate: Callable[[], None] | None = None,
) -> None:
    await socket.accept()
    relay = None
    tasks: list[asyncio.Task[None]] = []
    try:
        input = StreamOpenInput.model_validate(
            input_json(await asyncio.wait_for(socket.receive_text(), 10))
        )
        relay = await control.streams.open(device, handle, owner, input)

        async def receive() -> None:
            assert relay is not None
            while True:
                message = StreamAckInput.model_validate(input_json(await socket.receive_text()))
                if authenticate is not None:
                    authenticate()
                await control.streams.ack(relay, message)

        async def forward() -> None:
            assert relay is not None
            while True:
                if authenticate is not None:
                    authenticate()
                try:
                    frame = await asyncio.wait_for(relay.queue.get(), 1)
                except TimeoutError:
                    continue
                if isinstance(frame, StreamData):
                    # A frame may be consumed as soon as its first bytes are sent.
                    relay.delivered.add(int(frame.next_cursor))
                await asyncio.wait_for(socket.send_text(frame.model_dump_json()), 5)
                if isinstance(frame, StreamEnd):
                    # Let the consumer acknowledge its last data frame before
                    # closing; sending EOF must not race that cumulative ACK.
                    await asyncio.wait_for(relay.drained.wait(), 5)
                    return

        tasks = [asyncio.create_task(receive()), asyncio.create_task(forward())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
        await socket.close(code=1000)
    except (RACPError, ValueError, ValidationError) as exc:
        error = (
            exc.error
            if isinstance(exc, RACPError)
            else RACPError("INVALID_ARGUMENT", "invalid stream input").error
        )
        try:
            await asyncio.wait_for(
                socket.send_json({"type": "stream_error", "error": asdict(error)}), 5
            )
            await socket.close(code=1008)
        except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
            pass
    except (WebSocketDisconnect, OSError, TimeoutError):
        try:
            await socket.close(code=1012)
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if relay is not None:
            await control.streams.close(relay)
