"""Owner-authenticated binary native carrier, separate from operation journals."""

import asyncio
from collections.abc import Callable

from fastapi import WebSocket, WebSocketDisconnect
from racp_domain.models import RACPError
from racp_protocol.models import NativeStopped
from racp_protocol.native_duplex import parse_duplex_frame

from racp_gateway.native_carrier import NativeCarrierBroker
from racp_gateway.terminal_socket import input_json


async def native_socket(
    socket: WebSocket,
    device: str,
    handle: str,
    owner: str,
    broker: NativeCarrierBroker,
    authenticate: Callable[[], None],
) -> None:
    await socket.accept()
    relay = None
    tasks = []
    try:
        authenticate()
        relay = await broker.open(device, handle, owner)

        async def receive() -> None:
            assert relay is not None
            while True:
                raw = await socket.receive_text()
                authenticate()
                value = input_json(raw)
                if not isinstance(value, dict):
                    raise RACPError("INVALID_ARGUMENT", "Native frame must be an object")
                frame = parse_duplex_frame(value)
                await broker.send(relay, frame.model_dump())

        async def forward() -> None:
            assert relay is not None
            while True:
                authenticate()
                try:
                    frame = await asyncio.wait_for(relay.queue.get(), 1)
                except TimeoutError:
                    broker.check(relay)
                    continue
                authenticate()
                if not isinstance(frame, NativeStopped):
                    broker.check(relay)
                await asyncio.wait_for(socket.send_text(frame.model_dump_json()), 5)
                if isinstance(frame, NativeStopped):
                    return

        tasks = [asyncio.create_task(receive()), asyncio.create_task(forward())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except (RACPError, ValueError, WebSocketDisconnect, OSError, TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if relay:
            await broker.close(relay)
        try:
            await socket.close(code=1000)
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
