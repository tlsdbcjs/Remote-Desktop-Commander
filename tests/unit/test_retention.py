from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_sdk.journal import Journal


def request(
    id: str, operation: str = "filesystem.write", *, retain: bool = True
) -> dict[str, object]:
    return {
        "operation_id": id,
        "device_id": "device",
        "operation": operation,
        "payload": {"content": "private-content"},
        "idempotency_key": "private-key",
        "context": {"principal_id": "owner"},
        "retain_key": retain,
    }


def test_result_retention_boundary_tombstone_and_active_records(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.db")
    try:
        start = datetime.now(UTC)
        first, _ = journal.accept("scope", "key-hash", "payload-hash", request("op_first"))
        journal.transition(first["id"], "SUCCEEDED", result={"secret": "output-content"})
        journal.db.execute(
            "UPDATE operations SET updated_at=? WHERE id=?",
            (start.isoformat().replace("+00:00", "Z"), first["id"]),
        )
        active, _ = journal.accept("scope", "active-key", "payload", request("op_active"))
        journal.transition(active["id"], "RUNNING")
        assert (
            journal.compact(now=start + timedelta(hours=24) - timedelta(microseconds=1))[
                "outcomes_expired"
            ]
            == 0
        )
        assert journal.get(first["id"])["result"] == {"secret": "output-content"}
        assert journal.compact(now=start + timedelta(hours=24))["outcomes_expired"] == 1
        tomb = journal.db.execute("SELECT * FROM idempotency_tombstones").fetchone()
        assert (
            tomb["scope"],
            tomb["key_hash"],
            tomb["payload_hash"],
            tomb["operation_id"],
            tomb["outcome_available"],
        ) == ("scope", "key-hash", "payload-hash", first["id"], 0)
        stored = journal.get(first["id"])
        assert stored["result"] is None and stored["error"] is None
        assert "payload" not in stored["request"] and "idempotency_key" not in stored["request"]
        assert journal.get(active["id"])["state"] == "RUNNING"
        with pytest.raises(RACPError) as expired:
            journal.accept("scope", "key-hash", "payload-hash", request("op_new"))
        assert (
            expired.value.error.code == "OPERATION_EXPIRED"
            and expired.value.error.details["operation_id"] == first["id"]
        )
        with pytest.raises(RACPError) as conflict:
            journal.accept("scope", "key-hash", "changed", request("op_new"))
        assert conflict.value.error.code == "IDEMPOTENCY_CONFLICT"
        journal.close()
        journal = Journal(tmp_path / "journal.db")
        with pytest.raises(RACPError, match="cannot run again"):
            journal.accept("scope", "key-hash", "payload-hash", request("op_new"))
    finally:
        journal.close()


def test_capacity_cannot_erase_tombstones_and_ephemeral_reads_can_expire(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.db")
    journal.tombstone_limit = 1
    try:
        record, _ = journal.accept("scope", "key", "payload", request("op_write"))
        journal.transition(record["id"], "SUCCEEDED")
        journal.compact(now=datetime.now(UTC) + timedelta(days=2))
        with pytest.raises(RACPError) as full:
            journal.accept("scope", "new", "new-payload", request("op_new"))
        assert full.value.error.code == "RESOURCE_EXHAUSTED"
        assert full.value.error.details["next_action"] == "export_and_register_new_device"
        read, _ = journal.accept(
            "scope", "read-key", "read-payload", request("op_read", "filesystem.stat", retain=False)
        )
        journal.transition(read["id"], "SUCCEEDED", result={"value": 1})
        collected = journal.compact(now=datetime.now(UTC) + timedelta(days=2))
        assert collected["ephemeral_reads_removed"] == 1
        assert journal.db.execute("SELECT COUNT(*) FROM idempotency_tombstones").fetchone()[0] == 1
        assert journal.get("op_write")["state"] == "SUCCEEDED"
    finally:
        journal.close()


def test_unknown_resolutions_are_separate_deduplicated_and_retained(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.db")
    try:
        record, _ = journal.accept("scope", "key", "payload", request("op_unknown"))
        unknown = journal.transition(record["id"], "UNKNOWN", error={"code": "EXECUTION_UNKNOWN"})
        assert journal.transition(record["id"], "SUCCEEDED", result={"bytes": 3}) == unknown
        assert journal.transition(record["id"], "SUCCEEDED", result={"bytes": 3}) == unknown
        resolutions = journal.resolutions(record["id"])
        assert len(resolutions) == 1 and resolutions[0]["state"] == "SUCCEEDED"
        assert resolutions[0]["result"] == {"bytes": 3} and resolutions[0]["outcome_available"]
        with pytest.raises(RACPError):
            journal.compact(retention_seconds=86399)
        journal.compact(now=datetime.now(UTC) + timedelta(days=2))
        assert journal.resolutions(record["id"])[0]["outcome_available"] is False
        assert journal.resolutions(record["id"])[0]["result"] is None
        assert journal.get(record["id"])["state"] == "UNKNOWN"
    finally:
        journal.close()


def test_retention_preserves_pins_and_purges_audit_after_30_days(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.db")
    try:
        now = datetime.now(UTC)
        item, _ = journal.accept("scope", "key", "payload", request("op_pinned"))
        journal.transition(item["id"], "SUCCEEDED")
        journal.db.execute(
            "UPDATE operations SET updated_at=?",
            ((now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),),
        )
        journal.db.execute("DELETE FROM audit")
        journal.audit("old_audit", item["request"])
        journal.audit("boundary_audit", item["request"])
        journal.db.execute(
            "UPDATE audit SET timestamp=? WHERE event='old_audit'",
            ((now - timedelta(days=30, microseconds=1)).isoformat().replace("+00:00", "Z"),),
        )
        journal.db.execute(
            "UPDATE audit SET timestamp=? WHERE event='boundary_audit'",
            ((now - timedelta(days=30)).isoformat().replace("+00:00", "Z"),),
        )
        assert journal.compact(now=now, pinned_operations={item["id"]})["outcomes_expired"] == 0
        assert journal.get(item["id"])["outcome_available"]
        events = [row[0] for row in journal.db.execute("SELECT event FROM audit")]
        assert "old_audit" not in events and "boundary_audit" in events
        assert journal.compact(now=now)["outcomes_expired"] == 1
    finally:
        journal.close()


def test_forward_utc_jump_cannot_shorten_retention_and_os_restart_extends_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elapsed = [1000.0]
    monkeypatch.setattr("racp_sdk.journal.monotonic", lambda: elapsed[0])
    path = tmp_path / "journal.db"
    journal = Journal(path)
    base = datetime.now(UTC)

    class ForwardUTC(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return base + timedelta(days=3)

    try:
        first, _ = journal.accept("scope", "unknown", "payload", request("op_clock_unknown"))
        journal.transition(first["id"], "UNKNOWN")
        journal.transition(first["id"], "SUCCEEDED", result={"fact": "late"})
        second, _ = journal.accept("scope", "restart", "payload", request("op_clock_restart"))
        journal.transition(second["id"], "SUCCEEDED", result={"fact": "completed"})
        monkeypatch.setattr("racp_sdk.journal.datetime", ForwardUTC)
        assert journal.compact()["outcomes_expired"] == 0
        elapsed[0] += 86399
        assert journal.compact()["outcomes_expired"] == 0
        assert journal.resolutions(first["id"])[0]["outcome_available"]
        elapsed[0] += 1
        assert journal.compact(pinned_operations={second["id"]})["outcomes_expired"] == 1
        assert not journal.resolutions(first["id"])[0]["outcome_available"]
        journal.close()
        elapsed[0] = 100
        monkeypatch.setattr("racp_sdk.journal.clock_identity", lambda: "different-os-boot")
        journal = Journal(path)
        assert journal.compact()["outcomes_expired"] == 0
        assert journal.get(second["id"])["result"] == {"fact": "completed"}
        elapsed[0] += 86400
        assert journal.compact()["outcomes_expired"] == 1
    finally:
        journal.close()
