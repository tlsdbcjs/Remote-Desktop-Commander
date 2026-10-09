"""Queued terminal bytes must use the permission snapshot at actual transmission."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from racp_agent.runtime import Agent
from racp_domain.models import RACPError
from racp_policy.permissions import LocalPermissions, compile_permissions
from racp_protocol.models import Context, StreamData, StreamSubscribe
from racp_sdk.peer_writer import PeerWriter


async def test_revocation_while_writer_busy_does_not_transmit_terminal_data(tmp_path: Path) -> None:
    agent = Agent(
        "https://gateway.example",
        "fixture-device-secret",
        "dev_fixture",
        tmp_path,
        tmp_path / "agent",
        profile="trusted_personal",
        permissions=LocalPermissions(grants={"terminal.output.read": "allow"}),
    )
    entered, release = asyncio.Event(), asyncio.Event()
    sent: list[dict] = []

    async def socket_send(raw: str) -> None:
        if not sent:
            entered.set()
            await release.wait()
        sent.append(json.loads(raw))

    agent.writer = PeerWriter(socket_send)
    context = Context(
        principal_id="owner", execution_profile_id="trusted_personal", policy_revision=1
    )
    subscription = StreamSubscribe(
        device_id=agent.device_id,
        agent_boot_id=agent.boot_id,
        connection_epoch=1,
        stream_id="stream_fixture",
        handle_id="term_fixture",
        cursor="0",
        max_bytes=1024,
        window_bytes=1024,
        context=context,
    )
    agent.streams = SimpleNamespace(
        subscriptions={"stream_fixture": SimpleNamespace(request=subscription)}
    )
    control = asyncio.create_task(
        agent.send(
            SimpleNamespace(type="heartbeat", model_dump_json=lambda: '{"type":"heartbeat"}')
        )
    )
    data = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        data = asyncio.create_task(
            agent.send(
                StreamData(
                    device_id=agent.device_id,
                    agent_boot_id=agent.boot_id,
                    connection_epoch=1,
                    stream_id="stream_fixture",
                    handle_id="term_fixture",
                    byte_offset="0",
                    next_cursor="7",
                    data="PRIVATE",
                    invalid_byte_replacements=0,
                    eof=False,
                    process_exit=None,
                )
            )
        )
        await asyncio.sleep(0)
        agent.permissions = compile_permissions(LocalPermissions())
        release.set()
        await asyncio.wait_for(control, 2)
        with pytest.raises(RACPError) as raised:
            await asyncio.wait_for(data, 2)
        assert raised.value.error.code == "PERMISSION_DENIED"
        assert sent == [{"type": "heartbeat"}]
    finally:
        release.set()
        for task in (control, data):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *[task for task in (control, data) if task is not None], return_exceptions=True
        )
        await agent.writer.close()
        agent.journal.close()
