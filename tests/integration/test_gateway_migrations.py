"""Gateway-only migrations preserve shared identity and idempotency records."""

from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.management.store import ManagementStore
from racp_gateway.migrations import CURRENT_GATEWAY_SCHEMA, MIGRATIONS, migrate_gateway
from racp_gateway.store import GatewayStore
from racp_protocol.management import SettingsPatch
from racp_protocol.models import new_id
from racp_sdk.security import digest


def test_upgrade_preserves_identity_and_deduplication(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner-secret"))
    enrollment = store.enrollment("first")
    enrolled = store.enroll(enrollment)
    request = {
        "operation_id": new_id("op"),
        "device_id": enrolled["device_id"],
        "operation": "filesystem.stat",
        "context": {"principal_id": "owner_local"},
    }
    store.accept("owner_local", "same-key", "payload", request)
    before = {
        "owner": store.db.execute("SELECT * FROM owner").fetchall(),
        "devices": store.db.execute("SELECT * FROM devices").fetchall(),
        "credentials": store.db.execute("SELECT * FROM credentials").fetchall(),
        "operations": store.db.execute(
            "SELECT id,scope,key_hash,payload_hash FROM operations"
        ).fetchall(),
    }

    report = migrate_gateway(store.db, CURRENT_GATEWAY_SCHEMA)
    assert report.applied == ()
    assert before["owner"] == store.db.execute("SELECT * FROM owner").fetchall()
    assert before["devices"] == store.db.execute("SELECT * FROM devices").fetchall()
    assert before["credentials"] == store.db.execute("SELECT * FROM credentials").fetchall()
    assert before["operations"] == store.db.execute(
        "SELECT id,scope,key_hash,payload_hash FROM operations"
    ).fetchall()
    store.close()


def test_checksum_future_schema_and_repeated_apply_are_rejected_or_stable(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner-secret"))
    assert migrate_gateway(store.db).applied == ()
    store.db.execute("UPDATE gateway_schema SET checksum='bad' WHERE version=1")
    with pytest.raises(RuntimeError, match="checksum"):
        migrate_gateway(store.db)
    store.db.execute("DELETE FROM gateway_schema")
    store.db.execute(
        "INSERT INTO gateway_schema(version,checksum,applied_at) VALUES (99,'x','now')"
    )
    with pytest.raises(RuntimeError, match="future"):
        migrate_gateway(store.db)
    store.close()


def test_management_settings_compare_and_set(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner-secret"))
    management = ManagementStore(store)
    assert management.get_revision() == 1
    changed = management.compare_and_set(
        1, SettingsPatch(expected_revision=1, changes={"retention": {"audit_days": 90}})
    )
    assert changed.revision == 2
    assert changed.values["retention"]["audit_days"] == 90
    with pytest.raises(RACPError) as conflict:
        management.compare_and_set(1, SettingsPatch(expected_revision=1, changes={"x": 1}))
    assert conflict.value.error.code == "CONFLICT"
    store.close()


def test_migration_checksums_are_deterministic() -> None:
    assert CURRENT_GATEWAY_SCHEMA == max(MIGRATIONS)


def test_management_search_indexes_are_migrated_without_rewriting_v1(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner-secret"))
    try:
        versions = [
            int(row[0])
            for row in store.db.execute(
                "SELECT version FROM gateway_schema ORDER BY version"
            ).fetchall()
        ]
        assert versions == [1, 2]
        indexes = {
            str(row[0])
            for row in store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND "
                "name IN ('audit_time','audit_device_time','audit_request_time','audit_owner_time',"
                "'diagnostic_logs_request_time','diagnostic_logs_actor_time')"
            ).fetchall()
        }
        assert indexes == {
            "audit_time",
            "audit_device_time",
            "audit_request_time",
            "audit_owner_time",
            "diagnostic_logs_request_time",
            "diagnostic_logs_actor_time",
        }
    finally:
        store.close()
