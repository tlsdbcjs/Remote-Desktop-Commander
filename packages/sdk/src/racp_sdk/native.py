"""Bearer/TLS native carrier client; preserves the original native MCP protocol.

Endpoint connector and peer authorization are supplied by the trusted host-tool
launcher. This is a transport client, not a replacement MCP server or debugger.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import TypeAdapter
from racp_domain.models import RACPError
from racp_protocol.models import (
    Identifier,
    NativeOpened,
    NativePacket,
    NativeStopped,
    decode_message,
)
from racp_protocol.native_duplex import DuplexScope, NativeFrame, parse_duplex_frame
from websockets.asyncio.client import connect

from racp_sdk.security import require_secure_url, tls_context, websocket_tls_options


class NativeCarrierClient:
    def __init__(self, gateway: str, credential: str, *, ca_file: Path | None = None) -> None:
        require_secure_url(gateway)
        self.gateway, self.credential, self.ssl = gateway, credential, tls_context(ca_file)

    async def run(
        self,
        scope: DuplexScope,
        *,
        connector: Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
        | None = None,
        start: Callable[[], Awaitable[None]],
        stop: asyncio.Event,
        gate: Callable[[], None],
        max_bytes: int,
        lease_seconds: int,
        verify_peer: Callable[[asyncio.StreamWriter], None] | None = None,
        on_listen: Callable[[tuple[str, int]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        from racp_sdk.native_relay import NativeDuplexRelay

        if scope.endpoint_role == "agent_listener":
            if connector is None or verify_peer is not None or on_listen is not None:
                raise ValueError("Agent listener requires only a trusted host connector")
        elif connector is not None or verify_peer is None or on_listen is None:
            raise ValueError("Agent connector requires a verified host listener")

        identifier = TypeAdapter(Identifier)
        for value in (scope.device_id, scope.session_id):
            identifier.validate_python(value)
        parsed = urlsplit(self.gateway)
        url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                f"/api/v1/devices/{scope.device_id}/natives/{scope.session_id}/stream",
                "",
                "",
            )
        )
        require_secure_url(url, websocket=True)
        relay = None
        tasks = []
        deadline = time.monotonic() + lease_seconds
        try:
            async with connect(
                url,
                additional_headers={"Authorization": "Bearer " + self.credential},
                compression=None,
                max_size=65536,
                max_queue=4,
                open_timeout=10,
                **websocket_tls_options(url, self.ssl),
            ) as socket:
                opened = decode_message(await asyncio.wait_for(socket.recv(), 10))
                if (
                    not isinstance(opened, NativeOpened)
                    or DuplexScope.model_validate(opened.scope) != scope
                ):
                    raise RACPError("PERMISSION_DENIED", "Native ready authority differs")
                stream_id = opened.stream_id

                async def send(frame: NativeFrame) -> None:
                    gate()
                    await socket.send(frame.model_dump_json())

                relay = NativeDuplexRelay(
                    scope,
                    send,
                    gate=gate,
                    lease=lambda: deadline,
                    connect=connector,
                    max_bytes=max_bytes,
                    timeout_seconds=lease_seconds,
                )
                if scope.endpoint_role == "agent_connector":
                    assert verify_peer is not None and on_listen is not None
                    address = await relay.listen(verify_peer=verify_peer)
                    await on_listen(address)

                async def receive() -> None:
                    assert relay is not None
                    async for raw in socket:
                        gate()
                        message = decode_message(raw)
                        if not isinstance(message, (NativePacket, NativeStopped)):
                            raise RACPError("PROTOCOL_MISMATCH", "Unexpected native carrier frame")
                        if (
                            message.device_id,
                            message.agent_boot_id,
                            message.connection_epoch,
                            message.handle_id,
                            message.stream_id,
                        ) != (
                            scope.device_id,
                            scope.agent_boot_id,
                            scope.connection_epoch,
                            scope.session_id,
                            stream_id,
                        ):
                            raise RACPError("STALE_CONNECTION", "Native carrier envelope fenced")
                        if isinstance(message, NativeStopped):
                            if message.reason not in {"closed", "disconnected"}:
                                raise RACPError(
                                    "TRANSPORT_ERROR",
                                    "Native session stopped",
                                    reason=message.reason,
                                )
                            return
                        await relay.receive(parse_duplex_frame(message.frame))
                    raise RACPError("TRANSPORT_ERROR", "Native carrier closed without final state")

                async def activate() -> None:
                    await start()

                tasks = [
                    asyncio.create_task(receive()),
                    asyncio.create_task(activate()),
                    asyncio.create_task(stop.wait()),
                    asyncio.create_task(relay.closed.wait()),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if tasks[1] in done:
                    await tasks[1]
                    done, _ = await asyncio.wait(
                        [tasks[0], tasks[2], tasks[3]], return_when=asyncio.FIRST_COMPLETED
                    )
                for task in done:
                    await task
                return {
                    "channels": relay.channel_count,
                    "transferred_bytes": relay.transferred_bytes,
                }
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if relay:
                await relay.close()
