"""Authentication must be rechecked after waiting for stream input or output."""

import asyncio
import json
from types import SimpleNamespace

from racp_domain.models import RACPError
from racp_gateway.terminal_socket import terminal_socket


async def test_revocation_while_waiting_for_output_does_not_forward_the_frame() -> None:
    waiting = asyncio.Event()

    class Queue(asyncio.Queue):
        async def get(self):
            waiting.set()
            return await super().get()

    queue = Queue()
    relay = SimpleNamespace(queue=queue, delivered=set(), drained=asyncio.Event())
    authenticated = True
    frames = []
    close_calls = []
    received_open = False

    class Socket:
        async def accept(self):
            pass

        async def receive_text(self):
            nonlocal received_open
            if not received_open:
                received_open = True
                return json.dumps({"type": "stream_open", "cursor": "0"})
            await asyncio.Future()

        async def send_text(self, value):
            frames.append(json.loads(value))

        async def send_json(self, value):
            frames.append(value)

        async def close(self, code):
            close_calls.append(code)

    async def open_stream(*args):
        return relay

    async def close_stream(value):
        assert value is relay
        close_calls.append("relay")

    def authenticate():
        if not authenticated:
            raise RACPError("SESSION_EXPIRED", "owned session revoked")

    control = SimpleNamespace(streams=SimpleNamespace(open=open_stream, close=close_stream))
    task = asyncio.create_task(
        terminal_socket(Socket(), "dev_owned", "term_owned", "owner", control, authenticate)
    )
    await asyncio.wait_for(waiting.wait(), 2)
    authenticated = False
    queue.put_nowait(
        SimpleNamespace(
            model_dump_json=lambda: json.dumps({"type": "stream_data", "data": "must not disclose"})
        )
    )
    await asyncio.wait_for(task, 2)
    assert [frame["type"] for frame in frames] == ["stream_error"]
    assert frames[0]["error"]["code"] == "SESSION_EXPIRED"
    assert "relay" in close_calls


async def test_revocation_while_waiting_for_stream_open_does_not_dispatch() -> None:
    waiting = asyncio.Event()
    ready = asyncio.Event()
    authenticated = True
    opened = []
    errors = []

    class Socket:
        async def accept(self):
            pass

        async def receive_text(self):
            waiting.set()
            await ready.wait()
            return json.dumps({"type": "stream_open", "cursor": "0"})

        async def send_json(self, value):
            errors.append(value)

        async def close(self, code):
            pass

    async def open_stream(*args):
        opened.append(True)
        raise RACPError("SESSION_EXPIRED", "unexpected dispatch after revocation")

    def authenticate():
        if not authenticated:
            raise RACPError("SESSION_EXPIRED", "owned session revoked")

    control = SimpleNamespace(streams=SimpleNamespace(open=open_stream))
    task = asyncio.create_task(
        terminal_socket(Socket(), "dev_owned", "term_owned", "owner", control, authenticate)
    )
    await asyncio.wait_for(waiting.wait(), 2)
    authenticated = False
    ready.set()
    await asyncio.wait_for(task, 2)
    assert not opened
    assert errors[0]["error"]["code"] == "SESSION_EXPIRED"
