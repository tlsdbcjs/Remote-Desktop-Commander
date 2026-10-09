import base64
from types import SimpleNamespace

import pytest
from racp_domain.models import RACPError
from racp_gateway.native_carrier import NativeCarrierBroker
from racp_protocol.models import NativeOpened, NativePacket
from racp_protocol.native_duplex import DuplexData, DuplexScope


def fixture():
    scope = DuplexScope(
        session_id="native_own",
        device_id="dev_own",
        agent_boot_id="boot_own",
        connection_epoch=1,
        principal_id="owner",
        workspace_id="default",
        permission_revision="a" * 64,
    )
    sent = []

    async def send(message):
        sent.append(message)

    connection = SimpleNamespace(boot_id="boot_own", epoch=1, ready=True, send=send)
    device = {"revoked": False}

    def own_device(selected, owner):
        if selected != "dev_own" or owner != "owner":
            raise RACPError("PERMISSION_DENIED", "foreign device")
        return device

    handle = {
        "id": "native_own",
        "device_id": "dev_own",
        "owner": "owner",
        "agent_boot_id": "boot_own",
        "workspace_id": "default",
        "state": "ACTIVE",
        "backend": "racp-native-cdb",
    }

    def own_handle(selected, owner):
        if selected != "native_own" or owner != "owner":
            raise RACPError("PERMISSION_DENIED", "foreign handle")
        return handle

    control = SimpleNamespace(
        store=SimpleNamespace(device=own_device),
        handles=SimpleNamespace(get=own_handle),
        connections={"dev_own": connection},
    )
    broker = NativeCarrierBroker(control)
    value = {
        "native_scope": scope.model_dump(),
        "handle": handle,
        "max_bytes": 16384,
        "lease_seconds": 120,
    }
    broker.register(value, connection, "owner")
    return broker, scope, connection, device, sent


@pytest.mark.asyncio
async def test_subscriber_owner_device_and_single_consumer_are_enforced() -> None:
    broker, scope, connection, _, _ = fixture()
    for device, owner in [("dev_other", "owner"), ("dev_own", "other")]:
        with pytest.raises(RACPError):
            await broker.open(device, scope.session_id, owner)
    relay = await broker.open("dev_own", scope.session_id, "owner")
    with pytest.raises(RACPError) as error:
        await broker.open("dev_own", scope.session_id, "owner")
    assert error.value.error.code == "RESOURCE_BUSY"
    await broker.feed(NativeOpened(**relay.identity(), scope=scope.model_dump()), connection)
    assert relay.ready
    await broker.close(relay)


@pytest.mark.asyncio
async def test_epoch_and_scope_rejection_cannot_queue_payload() -> None:
    broker, scope, connection, _, _ = fixture()
    relay = await broker.open("dev_own", scope.session_id, "owner")
    await broker.feed(NativeOpened(**relay.identity(), scope=scope.model_dump()), connection)
    relay.queue.get_nowait()
    data = DuplexData(scope_fingerprint="b" * 64, channel_id=1, byte_offset=0, data_base64="eA==")
    with pytest.raises(RACPError):
        await broker.feed(NativePacket(**relay.identity(), frame=data.model_dump()), connection)
    assert relay.queue.empty()
    connection.epoch = 2
    with pytest.raises(RACPError):
        await broker.send(relay, data.model_dump())
    await broker.close(relay)


@pytest.mark.asyncio
async def test_revocation_discards_queued_bytes_and_unsubscribes_agent() -> None:
    broker, scope, connection, device, sent = fixture()
    relay = await broker.open("dev_own", scope.session_id, "owner")
    await broker.feed(NativeOpened(**relay.identity(), scope=scope.model_dump()), connection)
    data = DuplexData(
        scope_fingerprint=scope.fingerprint, channel_id=1, byte_offset=0, data_base64="eA=="
    )
    await broker.feed(NativePacket(**relay.identity(), frame=data.model_dump()), connection)
    device["revoked"] = True
    with pytest.raises(RACPError):
        broker.check(relay)
    await broker.close(relay, "revoked")
    assert relay.queue.qsize() == 1
    assert relay.queue.get_nowait().type == "native_stopped"
    assert sent[-1].type == "native_unsubscribe"
    assert not broker.relays


@pytest.mark.asyncio
async def test_shared_byte_budget_applies_to_both_directions() -> None:
    broker, scope, connection, _, _ = fixture()
    relay = await broker.open("dev_own", scope.session_id, "owner")
    await broker.feed(NativeOpened(**relay.identity(), scope=scope.model_dump()), connection)
    data = DuplexData(
        scope_fingerprint=scope.fingerprint,
        channel_id=1,
        byte_offset=0,
        data_base64=base64.b64encode(b"x" * 16384).decode(),
    )
    await broker.feed(NativePacket(**relay.identity(), frame=data.model_dump()), connection)
    with pytest.raises(RACPError) as error:
        await broker.send(
            relay,
            DuplexData(
                scope_fingerprint=scope.fingerprint, channel_id=1, byte_offset=0, data_base64="eA=="
            ).model_dump(),
        )
    assert error.value.error.code == "RESOURCE_EXHAUSTED"
    await broker.close(relay)
