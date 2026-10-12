"""Bounded strict JSON admission before FastAPI or mounted MCP parses request bodies."""

from racp_protocol.models import MAX_MESSAGE_BYTES, decode_json
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class JsonBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        media = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        binary_transfer = scope.get("method") == "PUT" and scope.get("path", "").endswith(
            "/content"
        )
        is_json = media == b"application/json" or media.endswith(b"+json") or not media
        if not is_json or binary_transfer:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await self._invalid(scope, receive, send)
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_MESSAGE_BYTES:
                await self._invalid(scope, receive, send, 413)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if body:
            try:
                decode_json(bytes(body))
            except (ValueError, RecursionError):
                await self._invalid(scope, receive, send)
                return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _invalid(scope: Scope, receive: Receive, send: Send, status: int = 400) -> None:
        response = JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": "INVALID_ARGUMENT" if status == 400 else "RESOURCE_EXHAUSTED",
                    "message": "invalid or oversized JSON document",
                    "layer": "gateway",
                    "retryable": False,
                    "execution_state": "not_started",
                    "details": {},
                }
            },
        )
        await response(scope, receive, send)
