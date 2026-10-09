from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.management.logs import LogStore
from racp_gateway.store import GatewayStore
from racp_protocol.management import LogQuery, ManagementPrincipal, SafeLogEntry
from racp_sdk.security import digest


def owner() -> ManagementPrincipal:
    return ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)


def test_log_search_redaction_time_range_and_cursor_scope(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    logs = LogStore(store)
    now = datetime.now(UTC)
    try:
        for index in range(3):
            logs.append(
                SafeLogEntry(
                    timestamp=(now + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
                    level="INFO",
                    event="test",
                    message="Authorization=raw-token <b>value</b>\x1b",
                    device_id="dev_one",
                    request_id=f"request-{index}",
                    actor_id="owner_local",
                    fields={"token": "never-store-this", "safe": "ok"},
                )
            )
        first = logs.search(owner(), LogQuery(limit=1))
        assert first.next_cursor
        rendered = first.items[0].model_dump_json()
        assert "raw-token" not in rendered
        assert "never-store-this" not in rendered
        assert "<b>" not in rendered
        with pytest.raises(RACPError) as mismatched:
            logs.search(owner(), LogQuery(device_id="dev_one", limit=1, cursor=first.next_cursor))
        assert mismatched.value.error.code == "CURSOR_EXPIRED"
        with pytest.raises(RACPError):
            logs.search(
                owner(),
                LogQuery(
                    from_utc="2026-01-02T00:00:00Z",
                    to_utc="2026-01-01T00:00:00Z",
                ),
            )
    finally:
        store.close()


def test_retention_never_deletes_idempotency_tombstones(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    logs = LogStore(store)
    try:
        request = {
            "operation_id": "op_tombstone",
            "device_id": "dev_one",
            "operation": "filesystem.write",
            "context": {"principal_id": "owner_local"},
        }
        record, _ = store.accept("scope", "key", "payload", request)
        store.transition(record["id"], "SUCCEEDED")
        store.compact(now=datetime.now(UTC) + timedelta(days=2))
        assert store.db.execute("SELECT COUNT(*) FROM idempotency_tombstones").fetchone()[0] == 1
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat().replace("+00:00", "Z")
        logs.append(SafeLogEntry(timestamp=old, level="INFO", event="old", message="old"))
        logs.compact(diagnostics_days=30, max_bytes=1024 * 1024)
        assert store.db.execute("SELECT COUNT(*) FROM idempotency_tombstones").fetchone()[0] == 1
    finally:
        store.close()


def test_audit_failure_blocks_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    enrolled = store.enroll(store.enrollment("agent"))
    device_id = enrolled["device_id"]
    try:
        monkeypatch.setattr(
            store,
            "audit",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
        )
        with pytest.raises(OSError, match="disk"):
            store.revoke(device_id)
        assert store.device(device_id)["revoked"] == 0
    finally:
        store.close()
