import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace

import psutil
import pytest
from racp_agent.providers.proxy import ProxyProvider
from racp_agent.tcp_peer import verify_tcp_peer
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import NativePacket, NativeSubscribe
from racp_protocol.native_duplex import DuplexScope, parse_duplex_frame
from racp_sdk.native_relay import NativeDuplexRelay


@pytest.mark.skipif(os.name != "nt", reason="Windows process-bound socket proof")
@pytest.mark.asyncio
async def test_proxy_connector_reaches_only_owned_server_and_preserves_binary():
    code = """import ctypes,socket,json
assert ctypes.WinDLL('shell32').IsUserAnAdmin()
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
print(json.dumps({'port':s.getsockname()[1]}),flush=True)
c,_=s.accept()
while data:=c.recv(65536):c.sendall(data)
c.close();s.close()
"""
    child = await asyncio.create_subprocess_exec(
        getattr(sys, "_base_executable", sys.executable),
        "-I",
        "-c",
        code,
        stdout=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    provider = None
    host = None
    carriers = []
    writer = None
    try:
        assert child.stdout is not None
        ready = json.loads(await asyncio.wait_for(child.stdout.readline(), 5))
        born = psutil.Process(child.pid).create_time()
        up, down = asyncio.Queue(64), asyncio.Queue(64)

        async def send(message):
            await up.put(message)

        provider = ProxyProvider(send, lambda: 1, lambda: float("inf"))
        context = ExecutionContext(
            "op_own", "req_own", "a" * 32, "dev_own", "owner", "boot_own", 10000
        )
        result = await provider.prepare(
            {
                "pid": child.pid,
                "create_time": born,
                "agent_boot_id": "boot_own",
                "direction": "agent_connector",
                "local_port": ready["port"],
            },
            context,
            "b" * 64,
            lambda: None,
        )
        scope = DuplexScope.model_validate(result["result"]["native_scope"])
        await provider.subscribe(
            NativeSubscribe(
                device_id="dev_own",
                agent_boot_id="boot_own",
                connection_epoch=1,
                stream_id="stream_own",
                handle_id=scope.session_id,
                principal_id="owner",
                workspace_id="default",
            )
        )
        assert (await up.get()).type == "native_opened"

        async def host_send(frame):
            await down.put(
                NativePacket(
                    device_id="dev_own",
                    agent_boot_id="boot_own",
                    connection_epoch=1,
                    stream_id="stream_own",
                    handle_id=scope.session_id,
                    frame=frame.model_dump(),
                )
            )

        host = NativeDuplexRelay(scope, host_send, gate=lambda: None, lease=lambda: float("inf"))
        own = psutil.Process()
        address = await host.listen(
            verify_peer=lambda writer: verify_tcp_peer(writer, own.pid, own.create_time())
        )

        async def to_agent():
            while True:
                await provider.receive(await down.get())

        async def to_host():
            while True:
                message = await up.get()
                if message.type == "native_packet":
                    await host.receive(parse_duplex_frame(message.frame))

        carriers = [asyncio.create_task(to_agent()), asyncio.create_task(to_host())]
        reader, writer = await asyncio.open_connection(*address)
        raw = bytes(range(256)) * 4
        writer.write(raw)
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(len(raw)), 5) == raw
        with pytest.raises(RACPError):
            verify_tcp_peer(writer, own.pid, own.create_time() + 1)
        assert provider.sessions[scope.session_id].scope.endpoint_role == "agent_connector"
        await provider.close(provider.sessions[scope.session_id])
        assert child.returncode is None
        with pytest.raises(RACPError, match="another context"):
            await provider.execute(
                "proxy.close",
                {"handle_id": scope.session_id},
                replace(context, workspace_id="other"),
                "b" * 64,
                lambda: None,
            )
        for _ in range(2):
            closed = await provider.execute(
                "proxy.close",
                {"handle_id": scope.session_id},
                context,
                "b" * 64,
                lambda: None,
            )
            assert closed["result"]["handle"]["state"] == "CLOSED"
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()
        for task in carriers:
            task.cancel()
        await asyncio.gather(*carriers, return_exceptions=True)
        if host:
            await host.close()
        if provider:
            await provider.shutdown()
        if child.returncode is None:
            child.terminate()
        await asyncio.wait_for(child.wait(), 5)
