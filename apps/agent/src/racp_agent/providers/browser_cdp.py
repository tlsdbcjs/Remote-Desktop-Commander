"""Bounded loopback CDP discovery and cleanup of explicitly authorized targets."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from racp_domain.models import RACPError
from racp_protocol.browser import cdp_endpoint
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException


async def resolve(endpoint: str) -> str:
    endpoint = cdp_endpoint(endpoint)
    parsed = urlsplit(endpoint)
    if parsed.scheme in {"ws", "wss"}:
        return endpoint
    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as http:
        async with http.stream("GET", endpoint.rstrip("/") + "/json/version") as response:
            if response.status_code != 200:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE", "CDP discovery unavailable", layer="provider"
                )
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise RACPError(
                        "RESOURCE_EXHAUSTED",
                        "CDP discovery response exceeds limit",
                        layer="provider",
                    )
    result = cdp_endpoint(str(json.loads(raw)["webSocketDebuggerUrl"]))
    found = urlsplit(result)
    if found.netloc != parsed.netloc or found.scheme != (
        "wss" if parsed.scheme == "https" else "ws"
    ):
        raise RACPError(
            "PERMISSION_DENIED", "CDP discovery changed endpoint scope", layer="provider"
        )
    return result


class CDP:
    def __init__(self, socket: ClientConnection) -> None:
        self.socket, self.sequence = socket, 0

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        await self.socket.send(
            json.dumps({"id": self.sequence, "method": method, "params": params or {}})
        )
        for _ in range(1024):
            message = json.loads(await self.socket.recv())
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RACPError("BROWSER_ERROR", "CDP command rejected", layer="provider")
                return dict(message.get("result", {}))
        raise RACPError("RESOURCE_EXHAUSTED", "CDP event bound exceeded", layer="provider")


@asynccontextmanager
async def peer(endpoint: str) -> AsyncIterator[CDP]:
    async with connect(
        cdp_endpoint(endpoint),
        open_timeout=3,
        close_timeout=0.2,
        max_size=1024**2,
        compression=None,
        proxy=None,
    ) as socket:
        yield CDP(socket)


async def targets(endpoint: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        resolved = await resolve(endpoint)
        async with peer(resolved) as cdp:
            entries = (await cdp.send("Target.getTargets"))["targetInfos"]
    pages = [item for item in entries if item.get("type") == "page"]
    return {
        "targets": [
            {
                "target_id": str(p["targetId"])[:128],
                "title": str(p.get("title", "")).encode()[:256].decode(errors="ignore"),
                "url": str(p.get("url", "")).encode()[:256].decode(errors="ignore"),
            }
            for p in pages[:64]
        ],
        "truncated": len(pages) > 64,
        "trust": "untrusted_browser_data",
    }


async def cleanup(scope: dict[str, Any], deadline: float) -> bool:
    if (
        not scope.get("context_id")
        and not scope.get("target_ids")
        and not scope.get("created_urls")
    ):
        return True
    try:
        async with asyncio.timeout_at(deadline):
            async with peer(scope["endpoint"]) as cdp:
                if scope.get("context_id"):
                    # Owned contexts are also disposeOnDetach in pinned Playwright.
                    contexts = (await cdp.send("Target.getBrowserContexts"))["browserContextIds"]
                    if scope["context_id"] in contexts:
                        await cdp.send(
                            "Target.disposeBrowserContext",
                            {"browserContextId": scope["context_id"]},
                        )
                current = (await cdp.send("Target.getTargets"))["targetInfos"]
                explicit = set(scope.get("target_ids", []))
                nonces = set(scope.get("created_urls", []))
                selected = {
                    t["targetId"]
                    for t in current
                    if t["targetId"] in explicit or t.get("url") in nonces
                }
                for target in selected:
                    await cdp.send("Target.closeTarget", {"targetId": target})
                while selected:
                    live = {
                        t["targetId"] for t in (await cdp.send("Target.getTargets"))["targetInfos"]
                    }
                    selected &= live
                    if selected:
                        await asyncio.sleep(0.02)
                return True
    except (TimeoutError, OSError, ValueError, KeyError, RACPError, WebSocketException):
        return False
