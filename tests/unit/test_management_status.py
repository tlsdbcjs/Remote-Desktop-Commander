import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from racp_gateway.config import GatewayConfig, write_gateway_config
from racp_gateway.management.status import GatewayStatusService
from racp_gateway.management.store import ManagementStore
from racp_gateway.store import GatewayStore
from racp_protocol.management import ManagementPrincipal
from racp_sdk.security import digest


class BusyDatabase:
    def __init__(self, delegate: sqlite3.Connection) -> None:
        self.delegate = delegate

    def execute(self, statement: str, *args: object, **kwargs: object) -> object:
        if statement == "SELECT 1":
            raise sqlite3.OperationalError("database is locked")
        return self.delegate.execute(statement, *args, **kwargs)


def test_status_reports_database_busy_separately(tmp_path: Path) -> None:
    state = tmp_path / "state"
    config_path = state / "config" / "gateway.json"
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_status_busy",
            state_root=str(state),
            public_origin="http://127.0.0.1:8765",
        ),
    )
    store = GatewayStore(state / "gateway.db")
    store.initialize(digest("owner"))
    real_db = store.db
    try:
        service = GatewayStatusService(
            store,
            ManagementStore(store),
            config_path=config_path,
            oauth_configured=False,
            event_stream_count=lambda: 0,
        )
        store.db = BusyDatabase(real_db)  # type: ignore[assignment]
        view = service.snapshot(
            ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)
        )
        assert view.database == "busy"
        assert view.ready is False
        assert "database is busy" in view.warnings
    finally:
        store.db = real_db
        store.close()


def test_status_does_not_count_stale_cached_online_device(tmp_path: Path) -> None:
    state = tmp_path / "state"
    config_path = state / "config" / "gateway.json"
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_status_stale",
            state_root=str(state),
            public_origin="http://127.0.0.1:8765",
        ),
    )
    store = GatewayStore(state / "gateway.db")
    store.initialize(digest("owner"))
    try:
        enrolled = store.enroll(store.enrollment("stale-agent"))
        store.connected(enrolled["device_id"], {"status": "ONLINE"})
        stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        store.db.execute(
            "UPDATE devices SET "
            "info=json_set(info,'$.status','ONLINE','$.last_seen_at',?) WHERE id=?",
            (stale, enrolled["device_id"]),
        )
        service = GatewayStatusService(
            store,
            ManagementStore(store),
            config_path=config_path,
            oauth_configured=False,
            event_stream_count=lambda: 0,
        )
        view = service.snapshot(
            ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)
        )
        assert view.total_devices == 1
        assert view.connected_devices == 0
        assert any("stale" in warning for warning in view.warnings)
    finally:
        store.close()


def test_status_marks_external_mcp_without_oauth_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    config_path = state / "config" / "gateway.json"
    config = GatewayConfig(
        instance_id="gateway_status_external",
        state_root=str(state),
        bind_address="127.0.0.1",
        public_origin="https://gateway.example",
    )
    monkeypatch.setattr("racp_gateway.management.status.load_gateway_config", lambda _: config)
    store = GatewayStore(state / "gateway.db")
    store.initialize(digest("owner"))
    try:
        view = GatewayStatusService(
            store,
            ManagementStore(store),
            config_path=config_path,
            oauth_configured=False,
            event_stream_count=lambda: 0,
        ).snapshot(ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1))
        assert view.mcp == "not_configured"
        assert "remote MCP OAuth is not configured" in view.warnings
    finally:
        store.close()
