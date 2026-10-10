import sqlite3
from collections import namedtuple
from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.backup import BackupManager
from racp_gateway.restore import RestoreManager
from racp_gateway.store import GatewayStore
from racp_sdk.security import digest


def test_online_backup_is_integrity_checked_and_preserves_device_identity(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "state" / "gateway.db")
    store.initialize(digest("owner"))
    enrolled = store.enroll(store.enrollment("backup fixture"))
    manager = BackupManager(
        store,
        tmp_path / "backups",
        instance_id="gateway_backup_test",
    )
    try:
        backup = manager.create("mnt_fixture")
        preview = RestoreManager(store).validate(backup.id)
        assert preview.valid is True
        assert preview.database_integrity == "ok"

        restored_path = RestoreManager(store).restore_database(
            backup.id,
            tmp_path / "restored" / "gateway.db",
        )
        snapshot = GatewayStore(restored_path)
        try:
            device = snapshot.device(enrolled["device_id"])
            assert device["name"] == "backup fixture"
        finally:
            snapshot.close()
    finally:
        store.close()


def test_corrupt_backup_is_not_restore_eligible(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "state" / "gateway.db")
    store.initialize(digest("owner"))
    manager = BackupManager(
        store,
        tmp_path / "backups",
        instance_id="gateway_backup_test",
    )
    try:
        backup = manager.create("mnt_fixture")
        database = Path(backup.manifest_path).parent / "gateway.db"
        with database.open("ab") as stream:
            stream.write(b"corruption")
        preview = RestoreManager(store).validate(backup.id)
        assert preview.valid is False
        assert preview.database_integrity == "failed"
    finally:
        store.close()


def test_backup_fails_before_writing_when_disk_space_is_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = GatewayStore(tmp_path / "state" / "gateway.db")
    store.initialize(digest("owner"))
    manager = BackupManager(store, tmp_path / "backups", instance_id="gateway_backup_test")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("racp_gateway.backup.shutil.disk_usage", lambda _: usage(100, 99, 1))
    try:
        with pytest.raises(RACPError) as error:
            manager.create("mnt_disk_low")
        assert error.value.error.code == "RESOURCE_EXHAUSTED"
        assert not (tmp_path / "backups").exists()
    finally:
        store.close()


def test_restore_stage_fails_before_copy_when_disk_space_is_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = GatewayStore(tmp_path / "state" / "gateway.db")
    store.initialize(digest("owner"))
    manager = BackupManager(store, tmp_path / "backups", instance_id="gateway_backup_test")
    try:
        backup = manager.create("mnt_restore_disk_low")
        usage = namedtuple("usage", "total used free")
        monkeypatch.setattr("racp_gateway.restore.shutil.disk_usage", lambda _: usage(100, 99, 1))
        destination = tmp_path / "restore-stage"
        with pytest.raises(RACPError) as error:
            RestoreManager(store).stage(backup.id, destination)
        assert error.value.error.code == "RESOURCE_EXHAUSTED"
        assert not destination.exists()
    finally:
        store.close()


def test_staged_restore_invalidates_old_credentials_and_marks_inflight_work_unknown(
    tmp_path: Path,
) -> None:
    store = GatewayStore(tmp_path / "state" / "gateway.db")
    store.initialize(digest("owner"))
    enrolled = store.enroll(store.enrollment("restore-old-state"))
    operation, _ = store.accept(
        "restore-fixture",
        "restore-key",
        "restore-payload",
        {
            "operation_id": "op_restore_pending",
            "device_id": enrolled["device_id"],
            "operation": "filesystem.stat",
            "context": {"principal_id": "owner_local"},
        },
    )
    manager = BackupManager(store, tmp_path / "backups", instance_id="gateway_backup_test")
    try:
        backup = manager.create("mnt_restore_security")
        store.revoke(enrolled["device_id"])
        staged = RestoreManager(store).stage(backup.id, tmp_path / "restore-stage")
        connection = sqlite3.connect(staged["gateway.db"])
        try:
            active_credentials = int(
                connection.execute(
                    "SELECT COUNT(*) FROM credentials WHERE device_id=? AND expires>0",
                    (enrolled["device_id"],),
                ).fetchone()[0]
            )
            restored_operation = connection.execute(
                "SELECT state,result FROM operations WHERE id=?",
                (operation["id"],),
            ).fetchone()
            restored_status = connection.execute(
                "SELECT json_extract(info,'$.status') FROM devices WHERE id=?",
                (enrolled["device_id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        assert active_credentials == 0
        assert restored_operation == ("UNKNOWN", None)
        assert restored_status == "OFFLINE"
    finally:
        store.close()
