from pathlib import Path

import pytest
from racp_agent.browser_events import BrowserOutbox
from racp_domain.models import RACPError
from racp_gateway.browser_events import BrowserEvents
from racp_gateway.events import EventFeed
from racp_gateway.handles import HandleCatalog
from racp_gateway.store import GatewayStore
from racp_protocol.models import BrowserState, Capability, ResourceHandle, timestamp
from racp_sdk.security import digest, token


def test_browser_outbox_bounds_replay_ack_and_preserves_health_on_gap() -> None:
    outbox = BrowserOutbox(2)
    outbox.push({"kind": "health", "state": "unavailable", "capability": {"name": "browser"}}, [])
    outbox.push({"kind": "navigation", "state": "observed"}, [])
    outbox.push({"kind": "inventory", "state": "active"}, [])
    assert len(outbox.pending) == 2
    assert outbox.pending[-1]["kind"] == "gap"
    assert outbox.pending[-1]["capability"] == {"name": "browser"}
    with pytest.raises(RACPError):
        outbox.ack("3")
    outbox.sent = 3
    outbox.ack("2")
    assert len(outbox.pending) == 1 and outbox.pending[0]["event_sequence"] == "3"
    outbox.ack("3")
    assert not outbox.pending


async def test_browser_event_owner_scope_atomic_dedupe_restart_and_gap_refresh(
    tmp_path: Path,
) -> None:
    file = tmp_path / "gateway.db"
    store = GatewayStore(file)
    store.initialize(digest(token()))
    device = store.enroll(store.enrollment("browser"))["device_id"]
    feed = EventFeed(store)
    store.event_feed = feed
    catalog = HandleCatalog(store)
    browser = BrowserEvents(store, catalog)
    now = timestamp()
    handle = ResourceHandle(
        id="browser_test",
        type="browser",
        device_id=device,
        owner="owner_local",
        agent_boot_id="boot_test",
        provider_instance_id="provider_test",
        resource_revision="1",
        created_at=now,
        last_access_at=now,
        expires_at=now,
        state="ACTIVE",
        availability="available",
    )
    message = BrowserState(
        device_id=device,
        agent_boot_id="boot_test",
        connection_epoch=1,
        provider_instance_id="provider_test",
        event_sequence="1",
        browser_id="browser_test",
        kind="inventory",
        state="active",
        handles=[handle],
    )
    assert browser.accept(message)
    assert not browser.accept(message)
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM audit WHERE event='browser_state_changed'"
        ).fetchone()[0]
        == 1
    )
    malformed = message.model_copy(
        update={
            "event_sequence": "2",
            "handles": [handle.model_copy(update={"owner": "owner_other"})],
        }
    )
    with pytest.raises(RACPError):
        browser.accept(malformed)
    assert store.db.execute("SELECT sequence FROM browser_event_cursors").fetchone()[0] == 1
    original = catalog.observe

    def fail_after_write(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        raise OSError("injected transaction failure")

    catalog.observe = fail_after_write  # type: ignore[method-assign]
    with pytest.raises(OSError):
        browser.accept(message.model_copy(update={"event_sequence": "2"}))
    assert store.db.execute("SELECT sequence FROM browser_event_cursors").fetchone()[0] == 1
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM audit WHERE event='browser_state_changed'"
        ).fetchone()[0]
        == 1
    )
    store.close()
    store = GatewayStore(file)
    store.initialize()
    feed = EventFeed(store)
    store.event_feed = feed
    browser = BrowserEvents(store, HandleCatalog(store))
    try:
        assert not browser.accept(message)
        cursor = f"{feed.id}:{feed.latest()}"
        assert browser.accept(
            message.model_copy(
                update={"event_sequence": "3", "kind": "gap", "state": "refresh_required"}
            )
        )
        stream, release = feed.stream("owner_local", cursor, lambda: None)
        await anext(stream)
        changes = await anext(stream)
        assert "browser_state_changed" in changes
        changes = await anext(stream)
        assert '"full_refresh":true' in changes and "event: refresh" in changes
        await stream.aclose()
        release()
        capability = Capability(name="browser", version="1.0.0", operations=["shell.exec"])
        with pytest.raises(RACPError):
            browser.accept(
                message.model_copy(
                    update={"event_sequence": "4", "kind": "health", "capability": capability}
                )
            )
        assert store.db.execute("SELECT sequence FROM browser_event_cursors").fetchone()[0] == 3
    finally:
        store.close()
