import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from racp_domain.models import RACPError
from racp_protocol.models import decode_message
from racp_protocol.streams import (
    StreamAckInput,
    StreamData,
    StreamEnd,
    StreamGap,
    StreamOpened,
    StreamOpenInput,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, InvalidStatus

from racp_sdk.security import require_secure_url, tls_context, websocket_tls_options


class TerminalStreamClient:
    def __init__(self, gateway: str, credential: str, *, ca_file: Path | None = None) -> None:
        require_secure_url(gateway)
        self.gateway, self.credential = gateway, credential
        self.ssl = tls_context(ca_file)

    async def events(
        self,
        device_id: str,
        handle_id: str,
        *,
        cursor: str = "0",
        max_bytes: int = 65536,
        window_bytes: int = 256 * 1024,
    ) -> AsyncGenerator[dict[str, Any], None]:
        input = StreamOpenInput(cursor=cursor, max_bytes=max_bytes, window_bytes=window_bytes)
        # Validate path identifiers with the same wire schema before interpolation.
        from pydantic import TypeAdapter
        from racp_protocol.models import Identifier

        identifier = TypeAdapter(Identifier)
        identifier.validate_python(device_id)
        identifier.validate_python(handle_id)
        parsed = urlsplit(self.gateway)
        url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                f"/api/v1/devices/{device_id}/terminals/{handle_id}/stream",
                "",
                "",
            )
        )
        require_secure_url(url, websocket=True)
        stream_id: str | None = None
        expected = int(cursor)
        boot, epoch = "", 0
        try:
            async with connect(
                url,
                additional_headers={"Authorization": "Bearer " + self.credential},
                compression=None,
                max_size=1024 * 1024,
                max_queue=4,
                open_timeout=10,
                **websocket_tls_options(url, self.ssl),
            ) as socket:
                await socket.send(input.model_dump_json())
                async for raw in socket:
                    value = json.loads(raw)
                    if value.get("type") == "stream_error":
                        error = value["error"]
                        raise RACPError(error["code"], error["message"], **error.get("details", {}))
                    message = decode_message(raw)
                    if not isinstance(message, (StreamOpened, StreamData, StreamGap, StreamEnd)):
                        raise RACPError("PROTOCOL_MISMATCH", "unexpected stream frame")
                    if message.device_id != device_id or message.handle_id != handle_id:
                        raise RACPError("PERMISSION_DENIED", "stream identity differs")
                    if stream_id is None:
                        stream_id, boot, epoch = (
                            message.stream_id,
                            message.agent_boot_id,
                            message.connection_epoch,
                        )
                    if (
                        message.stream_id != stream_id
                        or message.agent_boot_id != boot
                        or message.connection_epoch != epoch
                    ):
                        raise RACPError("STALE_CONNECTION", "stream frame is fenced")
                    if isinstance(message, StreamData):
                        if int(message.byte_offset) != expected:
                            raise RACPError("PROTOCOL_MISMATCH", "stream byte sequence differs")
                        expected = int(message.next_cursor)
                        cursor = message.next_cursor
                    yield message.model_dump()
                    if isinstance(message, StreamData):
                        # Requesting the next item acknowledges consumer completion.
                        try:
                            await socket.send(
                                StreamAckInput(
                                    stream_id=stream_id, byte_offset=cursor
                                ).model_dump_json()
                            )
                        except ConnectionClosedOK:
                            # The final EOF may already be buffered locally. Keep
                            # consuming it; lack of a valid stream_end remains an error.
                            pass
                    if isinstance(message, StreamEnd):
                        if message.error:
                            error = message.error
                            raise RACPError(
                                error["code"],
                                error["message"],
                                layer=error.get("layer", "transport"),
                                **error.get("details", {}),
                            )
                        return
                raise RACPError("TRANSPORT_ERROR", "stream closed before EOF", last_cursor=cursor)
        except InvalidStatus as exc:
            code = (
                "UNAUTHENTICATED"
                if exc.response.status_code == 401
                else "PERMISSION_DENIED"
                if exc.response.status_code == 403
                else "TRANSPORT_ERROR"
            )
            raise RACPError(
                code, "terminal stream connection rejected", layer="transport", last_cursor=cursor
            ) from exc
        except (ConnectionClosed, OSError) as exc:
            raise RACPError(
                "TRANSPORT_ERROR",
                "stream disconnected; reconnect with the consumed cursor",
                layer="transport",
                last_cursor=cursor,
            ) from exc
        except ValueError as exc:
            raise RACPError(
                "PROTOCOL_MISMATCH",
                "invalid terminal stream frame",
                layer="transport",
                last_cursor=cursor,
            ) from exc
