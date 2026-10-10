import json
import shutil
from pathlib import Path

import pytest
from racp_gateway.restore_service import apply_restore_handoff
from racp_gateway.store import GatewayStore
from racp_protocol.models import timestamp
from racp_sdk.security import digest


class FakeHost:
    def __init__(self, *, healthy: bool) -> None:
        self.is_healthy = healthy
        self.stops = 0
        self.starts = 0

    def stop(self) -> None:
        self.stops += 1

    def start(self) -> None:
        self.starts += 1

    def healthy(self, url: str) -> bool:
        assert url == "http://127.0.0.1:8765/healthz"
        return self.is_healthy


def _database(path: Path, job_id: str, label: str) -> None:
    store = GatewayStore(path)
    store.initialize(digest(f"owner-{label}"))
    now = timestamp()
    store.db.execute(
        "INSERT INTO maintenance_jobs("
        "id,kind,state,actor_id,realm_id,idempotency_key,created_at,updated_at,progress"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, "restore", "RUNNING", "owner_local", "realm_local", label, now, now, 0.5),
    )
    store.db.execute(
        "INSERT INTO diagnostic_logs(id,timestamp,level,event,message) VALUES (?,?,?,?,?)",
        (f"log_{label}", now, "INFO", "restore.fixture", label),
    )
    store.close()


def _handoff(tmp_path: Path, *, healthy: bool) -> tuple[Path, FakeHost]:
    root = tmp_path / "state"
    stage = root / "restores" / "mnt_fixture"
    rollback = root / "backups" / "pre"
    stage.mkdir(parents=True)
    rollback.mkdir(parents=True)
    job_id = "mnt_fixture"
    live_db = root / "gateway.db"
    staged_db = stage / "gateway.db"
    rollback_db = rollback / "gateway.db"
    _database(live_db, job_id, "live")
    _database(staged_db, job_id, "restored")
    shutil.copy2(live_db, rollback_db)
    live_config = root / "config" / "gateway.json"
    staged_config = stage / "gateway.json"
    rollback_config = rollback / "gateway.json"
    live_config.parent.mkdir(parents=True)
    live_config.write_text("old\n", encoding="utf-8")
    staged_config.write_text("new\n", encoding="utf-8")
    rollback_config.write_text("old\n", encoding="utf-8")
    receipt = root / "restores" / "receipts" / f"{job_id}.json"
    handoff = stage / "handoff.json"
    handoff.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job_id": job_id,
                "state_root": str(root.resolve()),
                "service_name": "RACP Gateway",
                "health_url": "http://127.0.0.1:8765/healthz",
                "receipt_path": str(receipt.resolve()),
                "replacements": [
                    {
                        "source": str(staged_db.resolve()),
                        "target": str(live_db.resolve()),
                        "rollback_source": str(rollback_db.resolve()),
                    },
                    {
                        "source": str(staged_config.resolve()),
                        "target": str(live_config.resolve()),
                        "rollback_source": str(rollback_config.resolve()),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return handoff, FakeHost(healthy=healthy)


def _job_state(database: Path) -> str:
    store = GatewayStore(database)
    try:
        return str(
            store.db.execute(
                "SELECT state FROM maintenance_jobs WHERE id='mnt_fixture'"
            ).fetchone()[0]
        )
    finally:
        store.close()


def test_restore_helper_replaces_state_and_records_success(tmp_path: Path) -> None:
    handoff, host = _handoff(tmp_path, healthy=True)
    result = apply_restore_handoff(handoff, host)
    root = tmp_path / "state"
    assert result["state"] == "SUCCEEDED"
    assert host.stops == 1 and host.starts == 1
    assert _job_state(root / "gateway.db") == "SUCCEEDED"
    assert (root / "config" / "gateway.json").read_text(encoding="utf-8") == "new\n"
    receipt = root / "restores" / "receipts" / "mnt_fixture.json"
    assert json.loads(receipt.read_text(encoding="utf-8"))["state"] == "SUCCEEDED"
    assert not handoff.exists()


def test_restore_helper_rolls_back_when_health_fails(tmp_path: Path) -> None:
    handoff, host = _handoff(tmp_path, healthy=False)
    with pytest.raises(RuntimeError, match="health"):
        apply_restore_handoff(handoff, host)
    root = tmp_path / "state"
    assert host.stops == 2 and host.starts == 2
    assert _job_state(root / "gateway.db") == "FAILED"
    assert (root / "config" / "gateway.json").read_text(encoding="utf-8") == "old\n"
    receipt = json.loads(
        (root / "restores" / "receipts" / "mnt_fixture.json").read_text(encoding="utf-8")
    )
    assert receipt["state"] == "FAILED"
    assert receipt["rolled_back"] is True
