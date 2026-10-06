import json
from pathlib import Path
from typing import Any

import pytest
from racp_domain.models import RACPError
from racp_gateway.service import Connection, ControlPlane
from racp_gateway.store import GatewayStore
from racp_protocol.models import ResourceHandle, timestamp
from racp_protocol.streams import StreamAckInput, StreamData, StreamOpened, StreamOpenInput
from racp_sdk.security import digest, token


class Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        pass


async def test_stream_credit_fencing_and_ack_boundaries(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest(token()))
    device = store.enroll(store.enrollment("fixture"))["device_id"]
    control = ControlPlane(store)
    socket = Socket()
    connection = Connection(socket, "boot_fixture", 1, {"terminal.read"}, ready=True)
    other = Connection(Socket(), "boot_fixture", 2, {"terminal.read"}, ready=True)
    control.connections[device] = connection
    now = timestamp()
    handle = ResourceHandle(
        id="term_fixture",
        type="terminal",
        device_id=device,
        owner="owner_local",
        agent_boot_id="boot_fixture",
        provider_instance_id="provider_fixture",
        resource_revision="1",
        created_at=now,
        last_access_at=now,
        expires_at=now,
        state="ACTIVE",
        availability="available",
    )
    control.handles.observe([handle], device, "boot_fixture", complete=True)
    relay = None
    try:
        relay = await control.streams.open(
            device, handle.id, "owner_local", StreamOpenInput(max_bytes=256, window_bytes=1024)
        )
        identity = relay.identity()
        opened = StreamOpened(**identity, cursor="0", max_bytes=256, window_bytes=1024)
        with pytest.raises(RACPError) as fenced:
            control.streams.feed(opened, other)
        assert fenced.value.error.code == "STALE_CONNECTION"
        control.streams.feed(opened, connection)
        for offset in (0, 256, 512, 768):
            control.streams.feed(
                StreamData(
                    **identity,
                    byte_offset=str(offset),
                    next_cursor=str(offset + 256),
                    data="x" * 256,
                ),
                connection,
            )
        with pytest.raises(RACPError) as over_credit:
            control.streams.feed(
                StreamData(**identity, byte_offset="1024", next_cursor="1280", data="x" * 256),
                connection,
            )
        assert over_credit.value.error.code == "RESOURCE_EXHAUSTED"
        with pytest.raises(RACPError) as undelivered:
            await control.streams.ack(
                relay, StreamAckInput(stream_id=relay.request.stream_id, byte_offset="256")
            )
        assert undelivered.value.error.code == "INVALID_ARGUMENT"
        relay.delivered.update((256, 512, 768, 1024))
        with pytest.raises(RACPError):
            await control.streams.ack(
                relay, StreamAckInput(stream_id="stream_other", byte_offset="256")
            )
        with pytest.raises(RACPError):
            await control.streams.ack(
                relay, StreamAckInput(stream_id=relay.request.stream_id, byte_offset="1")
            )
        await control.streams.ack(
            relay, StreamAckInput(stream_id=relay.request.stream_id, byte_offset="512")
        )
        assert relay.consumed == 512 and list(relay.boundaries) == [768, 1024]
        before = len(socket.sent)
        await control.streams.ack(
            relay, StreamAckInput(stream_id=relay.request.stream_id, byte_offset="256")
        )
        assert len(socket.sent) == before and relay.consumed == 512
        assert socket.sent[-1]["type"] == "stream_ack" and socket.sent[-1]["byte_offset"] == "512"
    finally:
        if relay is not None:
            await control.streams.close(relay)
        await connection.writer.close()
        await other.writer.close()
        store.close()
