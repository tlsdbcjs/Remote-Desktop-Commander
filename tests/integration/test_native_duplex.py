import asyncio

import pytest
from racp_agent.native_duplex import NativeDuplexRelay
from racp_domain.models import RACPError
from racp_protocol.native_duplex import DuplexAck, DuplexData, DuplexEnd, DuplexOpen, DuplexScope


def scope() -> DuplexScope:
    return DuplexScope(
        session_id="native_own",
        device_id="dev_own",
        agent_boot_id="boot_own",
        connection_epoch=1,
        principal_id="owner",
        workspace_id="default",
        permission_revision="a" * 64,
    )


@pytest.mark.asyncio
async def test_three_native_connections_are_independent_and_binary_exact() -> None:
    pairs: asyncio.Queue = asyncio.Queue()

    async def sink(reader, writer):
        await pairs.put((reader, writer))

    server = await asyncio.start_server(sink, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def connect():
        return await asyncio.open_connection("127.0.0.1", port)

    left = right = None
    left_wire, right_wire = asyncio.Queue(maxsize=64), asyncio.Queue(maxsize=64)

    async def to_right(frame):
        await right_wire.put(frame)

    async def to_left(frame):
        await left_wire.put(frame)

    async def carry(queue, relay):
        while True:
            await relay.receive(await queue.get())

    left = NativeDuplexRelay(scope(), to_right, gate=lambda: None, lease=lambda: float("inf"))
    right = NativeDuplexRelay(
        scope(), to_left, gate=lambda: None, lease=lambda: float("inf"), connect=connect
    )
    carriers = [
        asyncio.create_task(carry(left_wire, left)),
        asyncio.create_task(carry(right_wire, right)),
    ]
    try:
        address = await left.listen(verify_peer=lambda writer: None)
        inputs = [await asyncio.open_connection(*address) for _ in range(3)]
        sinks = [await asyncio.wait_for(pairs.get(), 2) for _ in range(3)]
        for index, ((reader, writer), (remote_reader, remote_writer)) in enumerate(
            zip(inputs, sinks, strict=True)
        ):
            raw = bytes(range(256)) * (index + 1)
            writer.write(raw)
            await writer.drain()
            assert await asyncio.wait_for(remote_reader.readexactly(len(raw)), 2) == raw
            remote_writer.write(raw[::-1])
            await remote_writer.drain()
            assert await asyncio.wait_for(reader.readexactly(len(raw)), 2) == raw[::-1]
        assert left.channel_count == right.channel_count == 3
        assert left.transferred_bytes == right.transferred_bytes == 3072
        for _, writer in inputs + sinks:
            writer.close()
            await writer.wait_closed()
    finally:
        for task in carriers:
            task.cancel()
        await asyncio.gather(*carriers, return_exceptions=True)
        await asyncio.wait_for(left.close(), 2)
        await asyncio.wait_for(right.close(), 2)
        server.close()
        await server.wait_closed()
    assert left.active_tasks == right.active_tasks == 0


@pytest.mark.asyncio
async def test_idle_session_permission_retirement_closes_owned_listener() -> None:
    allowed = True

    def gate():
        if not allowed:
            raise PermissionError("denied")

    async def send(frame):
        raise AssertionError("idle session must not send payload")

    relay = NativeDuplexRelay(scope(), send, gate=gate, lease=lambda: float("inf"))
    address = await relay.listen(verify_peer=lambda writer: None)
    allowed = False
    await asyncio.wait_for(relay.closed.wait(), 1)
    with pytest.raises(OSError):
        await asyncio.open_connection(*address)
    await relay.close()
    assert relay.active_tasks == 0


@pytest.mark.asyncio
async def test_peer_rejection_sends_no_native_open() -> None:
    sent = []

    async def send(frame):
        sent.append(frame)

    def reject(writer):
        raise PermissionError("wrong process")

    relay = NativeDuplexRelay(scope(), send, gate=lambda: None, lease=lambda: float("inf"))
    try:
        reader, writer = await asyncio.open_connection(*await relay.listen(verify_peer=reject))
        assert await asyncio.wait_for(reader.read(1), 1) == b""
        writer.close()
        await writer.wait_closed()
        assert sent == []
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_ack_credit_bounds_reads_and_only_accepts_sent_boundaries() -> None:
    sent = []
    four, eight = asyncio.Event(), asyncio.Event()

    async def send(frame):
        sent.append(frame)
        count = len([f for f in sent if isinstance(f, DuplexData)])
        if count == 4:
            four.set()
        elif count == 8:
            eight.set()

    relay = NativeDuplexRelay(scope(), send, gate=lambda: None, lease=lambda: float("inf"))
    try:
        address = await relay.listen(verify_peer=lambda writer: None)
        _, writer = await asyncio.open_connection(*address)
        writer.write(b"x" * (16384 * 10))
        await writer.drain()
        await asyncio.wait_for(four.wait(), 2)
        await asyncio.sleep(0.1)
        assert len([f for f in sent if isinstance(f, DuplexData)]) == 4
        assert relay.transferred_bytes == 65536
        await relay.receive(
            DuplexAck(scope_fingerprint=scope().fingerprint, channel_id=1, byte_offset=65536)
        )
        await asyncio.wait_for(eight.wait(), 2)
        with pytest.raises(RACPError) as raised:
            await relay.receive(
                DuplexAck(scope_fingerprint=scope().fingerprint, channel_id=1, byte_offset=65537)
            )
        assert raised.value.error.code == "INVALID_ARGUMENT"
        writer.close()
        await writer.wait_closed()
    finally:
        await relay.close()
    assert relay.closed.is_set() and relay.active_tasks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["scope", "offset", "replay", "budget", "after_end"])
async def test_invalid_inbound_never_reaches_owned_peer(violation: str) -> None:
    pairs = asyncio.Queue()

    async def accept(reader, writer):
        await pairs.put((reader, writer))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def connect():
        return await asyncio.open_connection("127.0.0.1", port)

    async def send(frame):
        pass

    relay = NativeDuplexRelay(
        scope(), send, gate=lambda: None, lease=lambda: float("inf"), connect=connect, max_bytes=1
    )
    remote_writer = None
    try:
        opened = DuplexOpen(scope_fingerprint=scope().fingerprint, channel_id=1)
        await relay.receive(opened)
        reader, remote_writer = await asyncio.wait_for(pairs.get(), 1)
        if violation == "after_end":
            await relay.receive(
                DuplexEnd(scope_fingerprint=scope().fingerprint, channel_id=1, byte_offset=0)
            )
        frame = DuplexData(
            scope_fingerprint="b" * 64 if violation == "scope" else scope().fingerprint,
            channel_id=1,
            byte_offset=1 if violation == "offset" else 0,
            data_base64="eHg=" if violation == "budget" else "eA==",
        )
        with pytest.raises(RACPError):
            await relay.receive(opened if violation == "replay" else frame)
        assert await asyncio.wait_for(reader.read(1), 1) == b""
        assert relay.closed.is_set()
    finally:
        await relay.close()
        if remote_writer:
            remote_writer.close()
            await remote_writer.wait_closed()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_idle_lease_expiry_retires_session() -> None:
    async def send(frame):
        raise AssertionError("expired session cannot send")

    relay = NativeDuplexRelay(scope(), send, gate=lambda: None, lease=lambda: 0)
    await asyncio.wait_for(relay.closed.wait(), 1)
    await relay.close()
    assert relay.active_tasks == 0


@pytest.mark.asyncio
async def test_retirement_during_listener_creation_cannot_leave_open_port(monkeypatch) -> None:
    async def send(frame):
        raise AssertionError("retired listener cannot send")

    allowed = True

    def gate():
        if not allowed:
            raise PermissionError("retired")

    relay = NativeDuplexRelay(scope(), send, gate=gate, lease=lambda: float("inf"))
    original = asyncio.start_server
    created = None
    address = None

    async def delayed(*args, **kwargs):
        nonlocal allowed, created, address
        created = await original(*args, **kwargs)
        address = created.sockets[0].getsockname()
        allowed = False
        await asyncio.wait_for(relay.closed.wait(), 1)
        return created

    monkeypatch.setattr(asyncio, "start_server", delayed)
    try:
        with pytest.raises((RACPError, PermissionError)):
            await relay.listen(verify_peer=lambda writer: None)
        with pytest.raises(OSError):
            await asyncio.open_connection(*address)
    finally:
        if created:
            created.close()
            await created.wait_closed()
        await relay.close()
