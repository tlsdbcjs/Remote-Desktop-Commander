import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from racp_domain.models import RACPError
from racp_gateway.update_manifest import VerifiedRelease
from racp_gateway.updater import UpdateJob, Updater, download_verified_package


def archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        for name, payload in entries.items():
            output.writestr(name, payload)
    return buffer.getvalue()


def release(payload: bytes, **changes: object) -> VerifiedRelease:
    values = {
        "schema_version": 1,
        "protocol_major": 1,
        "release_id": "rel_017",
        "version": "0.1.17",
        "platform": "win-x64",
        "schema_min": 1,
        "schema_max": 2,
        "min_updater_version": "0.1.0",
        "package_url": "https://updates.example/gateway.zip",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "key_id": "a" * 64,
        "expires_at": "2099-01-01T00:00:00Z",
        "schema_rollback_compatible": True,
        "manifest_sha256": "b" * 64,
    }
    values.update(changes)
    return VerifiedRelease(**values)


class Host:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.events: list[str] = []

    def stop_gateway(self) -> None:
        self.events.append("stop")

    def start_gateway(self) -> None:
        self.events.append("start")

    def health(self, release: VerifiedRelease) -> bool:
        self.events.append("health:" + release.version)
        return self.healthy

    def restore_pre_update(self, backup_id: str) -> None:
        self.events.append("restore:" + backup_id)


def test_stage_verifies_hash_and_rejects_traversal(tmp_path: Path) -> None:
    good = archive({"RACP-Gateway/runtime/python.exe": b"python"})
    updater = Updater(tmp_path / "staging", lambda url, size: good)
    staged = updater.stage(release(good))
    assert (staged.root / "release/RACP-Gateway/runtime/python.exe").is_file()

    bad = archive({"../escape.txt": b"escape"})
    unsafe = Updater(tmp_path / "unsafe", lambda url, size: bad)
    try:
        unsafe.stage(release(bad, release_id="rel_bad"))
    except Exception as exc:
        assert "unsafe path" in str(exc)
    else:
        raise AssertionError("archive traversal was accepted")
    assert not (tmp_path / "escape.txt").exists()


def test_download_rejects_redirect_and_size_mismatch() -> None:
    redirect = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"location": "https://other.example/gateway.zip"},
        )
    )
    with pytest.raises(RACPError, match="redirect"):
        download_verified_package(
            "https://updates.example/gateway.zip",
            3,
            transport=redirect,
        )

    wrong_size = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"four",
            headers={"content-length": "4"},
        )
    )
    with pytest.raises(RACPError):
        download_verified_package(
            "https://updates.example/gateway.zip",
            3,
            transport=wrong_size,
        )


def test_apply_switches_only_verified_staging_and_rolls_back_health_failure(tmp_path: Path) -> None:
    payload = archive({"gateway/runtime/python.exe": b"python"})
    verified = release(payload)
    updater = Updater(tmp_path / "staging", lambda url, size: payload)
    staged = updater.stage(verified)
    active = tmp_path / "active-release.json"
    active.write_text(
        json.dumps({"release_id": "rel_016", "version": "0.1.16"}),
        encoding="utf-8",
    )
    job = UpdateJob(
        id="upd_017",
        current_version="0.1.16",
        active_release_file=active,
        receipt_file=tmp_path / "update-receipt.json",
    )

    success_host = Host()
    success = updater.apply(job, verified, staged, success_host)
    assert success.state == "SUCCEEDED"
    assert json.loads(active.read_text(encoding="utf-8"))["release_id"] == "rel_017"
    assert success_host.events == ["stop", "start", "health:0.1.17"]
    duplicate = updater.apply(job, verified, staged, success_host)
    assert duplicate == success
    assert success_host.events == ["stop", "start", "health:0.1.17"]

    active.write_text(
        json.dumps({"release_id": "rel_016", "version": "0.1.16"}),
        encoding="utf-8",
    )
    failed_job = job.model_copy(
        update={"id": "upd_017_failed", "receipt_file": tmp_path / "failed-receipt.json"}
    )
    failed_host = Host(healthy=False)
    failed = updater.apply(failed_job, verified, staged, failed_host)
    assert failed.state == "FAILED"
    assert failed.phase == "rollback"
    assert json.loads(active.read_text(encoding="utf-8"))["release_id"] == "rel_016"


def test_apply_recovers_running_receipt_without_losing_previous_release(tmp_path: Path) -> None:
    payload = archive({"gateway/runtime/python.exe": b"python"})
    verified = release(payload)
    updater = Updater(tmp_path / "staging", lambda url, size: payload)
    staged = updater.stage(verified)
    active = tmp_path / "active-release.json"
    active.write_text(
        json.dumps({"release_id": "rel_017", "path": str(staged.root / "release")}),
        encoding="utf-8",
    )
    receipt = tmp_path / "update-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "job_id": "upd_recover",
                "release_id": "rel_017",
                "version": "0.1.17",
                "state": "RUNNING",
                "phase": "switched",
                "previous_release": "rel_016",
                "previous_release_path": "C:/Program Files/RACP/Gateway/0.1.16",
                "error": None,
                "updated_at": "2026-10-08T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    host = Host()
    result = updater.apply(
        UpdateJob(
            id="upd_recover",
            current_version="0.1.16",
            active_release_file=active,
            receipt_file=receipt,
        ),
        verified,
        staged,
        host,
    )
    assert result.state == "SUCCEEDED"
    assert result.previous_release == "rel_016"
    assert result.previous_release_path == "C:/Program Files/RACP/Gateway/0.1.16"
    assert host.events == ["stop", "start", "health:0.1.17"]


@pytest.mark.parametrize("phase", ["staged", "stopped", "switched"])
def test_apply_recovers_each_persisted_running_phase(tmp_path: Path, phase: str) -> None:
    payload = archive({"gateway/runtime/python.exe": b"python"})
    verified = release(payload)
    updater = Updater(tmp_path / "staging", lambda url, size: payload)
    staged = updater.stage(verified)
    previous_path = "C:/Program Files/RACP/Gateway/0.1.16"
    active = tmp_path / "active-release.json"
    if phase == "switched":
        active.write_text(
            json.dumps(
                {
                    "release_id": verified.release_id,
                    "version": verified.version,
                    "path": str(staged.root / "release"),
                }
            ),
            encoding="utf-8",
        )
    else:
        active.write_text(
            json.dumps({"release_id": "rel_016", "path": previous_path}),
            encoding="utf-8",
        )
    receipt = tmp_path / f"{phase}-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "job_id": f"upd_recover_{phase}",
                "release_id": verified.release_id,
                "version": verified.version,
                "state": "RUNNING",
                "phase": phase,
                "previous_release": "rel_016",
                "previous_release_path": previous_path,
                "error": None,
                "updated_at": "2026-10-08T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    host = Host()
    result = updater.apply(
        UpdateJob(
            id=f"upd_recover_{phase}",
            current_version="0.1.16",
            active_release_file=active,
            receipt_file=receipt,
        ),
        verified,
        staged,
        host,
    )
    assert result.state == "SUCCEEDED"
    assert result.previous_release == "rel_016"
    assert result.previous_release_path == previous_path
    assert json.loads(active.read_text(encoding="utf-8"))["release_id"] == verified.release_id


def test_incompatible_schema_failure_restores_pre_update_backup(tmp_path: Path) -> None:
    payload = archive({"gateway/runtime/python.exe": b"python"})
    verified = release(payload, schema_rollback_compatible=False)
    updater = Updater(tmp_path / "staging", lambda url, size: payload)
    staged = updater.stage(verified)
    active = tmp_path / "active-release.json"
    active.write_text(
        json.dumps(
            {
                "release_id": "rel_016",
                "path": "C:/Program Files/RACP/Gateway/0.1.16",
            }
        ),
        encoding="utf-8",
    )
    job = UpdateJob(
        id="upd_incompatible",
        current_version="0.1.16",
        active_release_file=active,
        receipt_file=tmp_path / "incompatible-receipt.json",
        pre_update_backup_id="bkp_pre_update",
    )
    host = Host(healthy=False)
    failed = updater.apply(job, verified, staged, host)
    assert failed.state == "FAILED"
    assert failed.phase == "rollback"
    assert host.events == [
        "stop",
        "start",
        "health:0.1.17",
        "restore:bkp_pre_update",
        "start",
    ]
    restored = json.loads(active.read_text(encoding="utf-8"))
    assert restored["release_id"] == "rel_016"
    assert restored["rollback"] is True
