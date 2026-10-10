"""Out-of-process Windows restore helper for staged Gateway state replacement."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Protocol

import httpx
from racp_protocol.models import new_id, timestamp


class RestoreHost(Protocol):
    def stop(self) -> None: ...

    def start(self) -> None: ...

    def healthy(self, url: str) -> bool: ...


class WindowsRestoreHost:
    def __init__(self, service_name: str) -> None:
        self.service_name = service_name

    def stop(self) -> None:
        import win32serviceutil

        try:
            win32serviceutil.StopService(self.service_name)
        except Exception as exc:
            if "1062" not in str(exc):
                raise
        win32serviceutil.WaitForServiceStatus(self.service_name, 1, 30)

    def start(self) -> None:
        import win32serviceutil

        win32serviceutil.StartService(self.service_name)
        win32serviceutil.WaitForServiceStatus(self.service_name, 4, 30)

    def healthy(self, url: str) -> bool:
        try:
            response = httpx.get(
                url,
                verify=False,
                follow_redirects=False,
                trust_env=False,
                timeout=5,
            )
            return response.status_code == 200 and response.json() == {"status": "ok"}
        except (httpx.HTTPError, ValueError):
            return False


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("restore handoff path escapes state root")
    if path.exists() and path.is_symlink():
        raise ValueError("restore handoff path must not be a symlink")
    return resolved


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _set_job(database: Path, job_id: str, state: str, payload: dict[str, object]) -> None:
    connection = sqlite3.connect(database)
    try:
        receipt_id = new_id("mrc")
        now = timestamp()
        row = connection.execute(
            "SELECT kind FROM maintenance_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ValueError("staged restore is missing its maintenance job")
        connection.execute(
            "INSERT OR REPLACE INTO maintenance_receipts("
            "id,job_id,kind,state,payload,created_at) VALUES (?,?,?,?,?,?)",
            (receipt_id, job_id, row[0], state, json.dumps(payload, sort_keys=True), now),
        )
        connection.execute(
            "UPDATE maintenance_jobs SET state=?,updated_at=?,progress=1,error=?,receipt_id=? "
            "WHERE id=?",
            (
                state,
                now,
                None if state == "SUCCEEDED" else str(payload.get("error", "")),
                receipt_id,
                job_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def apply_restore_handoff(path: Path, host: RestoreHost) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError("invalid restore handoff file")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported restore handoff schema")
    state_root = Path(str(document["state_root"])).resolve(strict=True)
    handoff = _inside(path, state_root)
    receipt = _inside(Path(str(document["receipt_path"])), state_root)
    replacements: list[tuple[Path, Path, Path | None]] = []
    database: Path | None = None
    for item in document["replacements"]:
        source = _inside(Path(str(item["source"])), state_root)
        target = _inside(Path(str(item["target"])), state_root)
        rollback = item.get("rollback_source")
        rollback_source = _inside(Path(str(rollback)), state_root) if rollback else None
        if not source.is_file():
            raise ValueError("restore source file is missing")
        if target.name == "gateway.db":
            database = source
        replacements.append((source, target, rollback_source))
    if database is None:
        raise ValueError("restore handoff has no Gateway database")
    job_id = str(document["job_id"])
    success_payload: dict[str, object] = {
        "state": "SUCCEEDED",
        "job_id": job_id,
        "applied_at": timestamp(),
    }
    _set_job(database, job_id, "SUCCEEDED", success_payload)
    host.stop()
    applied: list[tuple[Path, Path | None]] = []
    try:
        for source, target, rollback_source in replacements:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".restore-apply.tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            applied.append((target, rollback_source))
        host.start()
        if not host.healthy(str(document["health_url"])):
            raise RuntimeError("restored Gateway failed health verification")
        _write_receipt(receipt, success_payload)
        handoff.unlink(missing_ok=True)
        return success_payload
    except BaseException as exc:
        try:
            host.stop()
        except BaseException:
            pass
        for target, rollback_source in reversed(applied):
            if rollback_source is None:
                continue
            temporary = target.with_name(target.name + ".restore-rollback.tmp")
            shutil.copy2(rollback_source, temporary)
            os.replace(temporary, target)
        rollback_payload: dict[str, object] = {
            "state": "FAILED",
            "job_id": job_id,
            "rolled_back": True,
            "error": str(exc)[:2048],
            "updated_at": timestamp(),
        }
        target_database = next(
            (target for target, _ in applied if target.name == "gateway.db"),
            None,
        )
        if target_database is not None and target_database.is_file():
            _set_job(target_database, job_id, "FAILED", rollback_payload)
        try:
            host.start()
        finally:
            _write_receipt(receipt, rollback_payload)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=0, choices=None)
    args = parser.parse_args()
    if not 0 <= args.delay_seconds <= 10:
        raise ValueError("restore helper delay is outside its bound")
    if sys.platform != "win32":
        raise OSError("Gateway restore helper requires Windows")
    if args.delay_seconds:
        time.sleep(args.delay_seconds)
    document = json.loads(args.handoff.read_text(encoding="utf-8"))
    apply_restore_handoff(args.handoff, WindowsRestoreHost(str(document["service_name"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
