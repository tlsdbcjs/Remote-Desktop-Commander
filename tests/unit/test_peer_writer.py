import asyncio
import json
import time
from typing import Any

from racp_domain.models import RACPError
from racp_sdk.peer_writer import PeerWriter


class Frame:
    def __init__(self, type: str, id: int) -> None:
        self.type, self.id = type, id

    def model_dump_json(self) -> str:
        return json.dumps({"type": self.type, "id": self.id})


async def test_reserved_control_queue_precedes_backlogged_data_and_single_writer() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    sent: list[dict[str, Any]] = []
    active = 0

    async def transport(text: str) -> None:
        nonlocal active
        active += 1
        assert active == 1
        if not sent:
            started.set()
            await release.wait()
        sent.append(json.loads(text))
        active -= 1

    writer = PeerWriter(transport)
    tasks = [asyncio.create_task(writer.send(Frame("stream_data", 0)))]
    await started.wait()
    tasks += [
        asyncio.create_task(writer.send(Frame("stream_data", index))) for index in range(1, 11)
    ]
    control = asyncio.create_task(writer.send(Frame("cancel", 99)))
    tasks.append(control)
    await asyncio.sleep(0)
    assert writer.data.qsize() == 8 and writer.control.qsize() == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert sent[1] == {"type": "cancel", "id": 99}
    assert len(sent) == 12
    await writer.close()


async def test_peer_failure_releases_queued_callers() -> None:
    ready = asyncio.Event()

    async def broken(text: str) -> None:
        await ready.wait()
        raise ConnectionError("injected transport failure")

    writer = PeerWriter(broken)
    tasks = [
        asyncio.create_task(writer.send(Frame("stream_data", 1))),
        asyncio.create_task(writer.send(Frame("heartbeat", 2))),
    ]
    await asyncio.sleep(0)
    ready.set()
    outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
    assert all(isinstance(result, ConnectionError) for result in outcomes)
    await writer.close()


async def test_expired_request_is_never_written_after_transport_backlog() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    sent: list[dict[str, Any]] = []

    async def transport(text: str) -> None:
        if not sent:
            started.set()
            await release.wait()
        sent.append(json.loads(text))

    writer = PeerWriter(transport)
    held = asyncio.create_task(writer.send(Frame("stream_data", 1)))
    await started.wait()
    deadline = time.monotonic() + 0.02

    def prepare() -> str:
        if time.monotonic() >= deadline:
            raise RACPError("TIMEOUT", "expired before transport", execution_state="not_started")
        return Frame("request", 2).model_dump_json()

    request = asyncio.create_task(writer.send(Frame("request", 2), prepare=prepare))
    heartbeat = asyncio.create_task(writer.send(Frame("heartbeat", 3)))
    await asyncio.sleep(0.03)
    release.set()
    results = await asyncio.gather(held, request, heartbeat, return_exceptions=True)
    assert isinstance(results[1], RACPError) and results[1].error.execution_state == "not_started"
    assert [frame["type"] for frame in sent] == ["stream_data", "heartbeat"]
    assert not writer.closed
    await writer.close()
