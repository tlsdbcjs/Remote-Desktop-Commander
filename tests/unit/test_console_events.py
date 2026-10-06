import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from racp_domain.models import RACPError
from racp_gateway.events import EventFeed, EventResponse
from racp_gateway.store import GatewayStore
from starlette.requests import ClientDisconnect


async def test_event_replay_owner_isolation_time_gap_and_failed_headers_release(
    tmp_path: Path,
) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize()
    feed = EventFeed(store)
    try:
        initial = f"{feed.id}:0"
        feed.publish("owner_a", "device_status_changed", {"device_id": "dev_a"})
        feed.publish("owner_b", "device_status_changed", {"device_id": "dev_b"})
        stream, release = feed.stream("owner_a", initial, lambda: None)
        await anext(stream)
        frame = await anext(stream)
        assert "dev_a" in frame and "dev_b" not in frame
        sequence = json.loads(frame.split("data: ")[1])["event_id"]
        await stream.aclose()
        release()
        assert not feed.active
        store.db.execute(
            "UPDATE console_events SET observed_at=?",
            ((datetime.now(UTC) - timedelta(seconds=601)).isoformat().replace("+00:00", "Z"),),
        )
        assert feed.cursor(initial)[1]
        assert feed.latest() == 2
        store.close()
        store = GatewayStore(tmp_path / "gateway.db")
        store.initialize()
        feed = EventFeed(store)
        assert feed.cursor(sequence)[1]
        assert feed.latest() == 2
        stream, release = feed.stream("owner_a", None, lambda: None)
        response = EventResponse(stream, release)

        async def broken_send(message: Any) -> None:
            raise ConnectionResetError("injected disconnect before response headers")

        async def receive() -> dict[str, str]:
            return {"type": "http.disconnect"}

        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, broken_send)
        assert not feed.active
    finally:
        store.close()


async def test_event_capacity_and_rolled_back_event_are_not_visible(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize()
    feed = EventFeed(store)
    try:
        with pytest.raises(RuntimeError), store.transaction():
            feed.publish("owner_local", "must_not_replay", {})
            raise RuntimeError("injected journal rollback")
        assert feed.latest() == 0
        held = [feed.stream("owner_local", None, lambda: None) for _ in range(16)]
        with pytest.raises(RACPError) as rejected:
            feed.stream("owner_local", None, lambda: None)
        assert rejected.value.error.code == "RESOURCE_EXHAUSTED"
        for stream, release in held:
            await stream.aclose()
            release()
        assert not feed.active
    finally:
        store.close()
