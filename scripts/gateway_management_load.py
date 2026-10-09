"""Measure isolated Gateway management metadata and SQLite load targets."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import psutil
from racp_domain.version import VERSION
from racp_gateway.management.devices import DeviceManagement
from racp_gateway.management.logs import LogStore
from racp_gateway.store import GatewayStore
from racp_protocol.management import DeviceQuery, LogQuery, ManagementPrincipal
from racp_protocol.models import timestamp
from racp_sdk.security import digest


def _p95(samples: list[float]) -> float:
    if not samples:
        raise ValueError("at least one latency sample is required")
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _seed_devices(store: GatewayStore, count: int) -> None:
    observed = timestamp()
    batch = 5000
    for start in range(0, count, batch):
        stop = min(count, start + batch)
        rows = [
            (
                f"dev_load_{index:08d}",
                f"load-agent-{index:08d}",
                "owner_local",
                json.dumps(
                    {
                        "status": "ONLINE",
                        "last_seen_at": observed,
                        "last_observed_at": observed,
                        "agent_version": VERSION,
                        "platform": "synthetic-windows-x64",
                    },
                    separators=(",", ":"),
                ),
            )
            for index in range(start, stop)
        ]
        metadata = [
            (f"dev_load_{index:08d}", "realm_local", "[]", 1, observed)
            for index in range(start, stop)
        ]
        with store.transaction():
            store.db.executemany(
                "INSERT INTO devices(id,name,owner_id,info) VALUES (?,?,?,?)",
                rows,
            )
            store.db.executemany(
                "INSERT INTO device_management(device_id,realm_id,tags,revision,updated_at) "
                "VALUES (?,?,?,?,?)",
                metadata,
            )


def _seed_audit(store: GatewayStore, count: int) -> None:
    observed = timestamp()
    batch = 20_000
    for start in range(0, count, batch):
        stop = min(count, start + batch)
        rows = [
            (
                f"aud_load_{index:010d}",
                observed,
                "load_fixture",
                f"dev_load_{index % 10_000:08d}",
                "",
                "filesystem.stat",
                "",
                f"request-load-{index:010d}",
                "{}",
                "owner_local",
            )
            for index in range(start, stop)
        ]
        with store.transaction():
            store.db.executemany(
                "INSERT INTO audit("
                "id,timestamp,event,device_id,operation_id,operation,trace_id,request_id,summary,owner_id"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )


def _latencies(call: Any, iterations: int) -> list[float]:
    call()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        call()
        samples.append(time.perf_counter() - started)
    return samples


def _writer_contention(store: GatewayStore, database: Path) -> dict[str, Any]:
    blocker = sqlite3.connect(database, isolation_level=None, timeout=0.1)
    blocked = False
    error = None
    started = time.perf_counter()
    try:
        blocker.execute("BEGIN IMMEDIATE")
        try:
            store.db.execute(
                "INSERT INTO diagnostic_logs("
                "id,timestamp,level,event,message,device_id,request_id,actor_id,fields_json"
                ") VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "log_contention_probe",
                    timestamp(),
                    "INFO",
                    "load.contention",
                    "probe",
                    "",
                    "",
                    "owner_local",
                    "{}",
                ),
            )
        except sqlite3.OperationalError as exc:
            blocked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            error = str(exc)
    finally:
        elapsed = time.perf_counter() - started
        try:
            blocker.execute("ROLLBACK")
        finally:
            blocker.close()
    store.db.execute(
        "INSERT INTO diagnostic_logs("
        "id,timestamp,level,event,message,device_id,request_id,actor_id,fields_json"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "log_contention_recovery",
            timestamp(),
            "INFO",
            "load.contention_recovery",
            "probe",
            "",
            "",
            "owner_local",
            "{}",
        ),
    )
    store.db.execute("DELETE FROM diagnostic_logs WHERE id LIKE 'log_contention_%'")
    return {
        "writer_was_blocked": blocked,
        "blocked_seconds": round(elapsed, 6),
        "error": error,
        "write_after_release": "pass",
    }


def run_load(
    evidence_dir: Path,
    *,
    devices: int,
    audit_rows: int,
    iterations: int,
    target_seconds: float,
) -> dict[str, Any]:
    if evidence_dir.exists():
        raise FileExistsError("load evidence directory must be new")
    if devices < 50 or audit_rows < 1 or iterations < 1 or target_seconds <= 0:
        raise ValueError("invalid Gateway management load parameters")
    evidence_dir.mkdir(parents=True)
    fixture = evidence_dir / "fixture"
    database = fixture / "gateway.db"
    process = psutil.Process()
    started_at = timestamp()
    rss_before = process.memory_info().rss
    store: GatewayStore | None = None
    result: dict[str, Any] = {}
    try:
        store = GatewayStore(database)
        store.initialize(digest("load-fixture-owner"))
        seed_started = time.perf_counter()
        _seed_devices(store, devices)
        _seed_audit(store, audit_rows)
        seed_seconds = time.perf_counter() - seed_started
        principal = ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)
        device_management = DeviceManagement(store)
        logs = LogStore(store)

        device_samples = _latencies(
            lambda: device_management.list(principal, DeviceQuery(limit=50)),
            iterations,
        )
        audit_samples = _latencies(
            lambda: logs.search(principal, LogQuery(source="audit", limit=50)),
            iterations,
        )
        contention = _writer_contention(store, database)
        device_p95 = _p95(device_samples)
        audit_p95 = _p95(audit_samples)
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        result = {
            "version": VERSION,
            "started_at": started_at,
            "finished_at": timestamp(),
            "host": {
                "computer_name": os.environ.get("COMPUTERNAME", "unknown"),
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
            },
            "fixture": {
                "synthetic_devices": devices,
                "audit_rows": audit_rows,
                "page_size": 50,
                "iterations": iterations,
                "seed_seconds": round(seed_seconds, 3),
                "database_bytes": database.stat().st_size,
            },
            "device_metadata": {
                "p95_seconds": round(device_p95, 6),
                "max_seconds": round(max(device_samples), 6),
                "target_seconds": target_seconds,
                "target_met": device_p95 <= target_seconds,
            },
            "audit_search": {
                "p95_seconds": round(audit_p95, 6),
                "max_seconds": round(max(audit_samples), 6),
                "target_seconds": target_seconds,
                "target_met": audit_p95 <= target_seconds,
            },
            "sqlite_writer_contention": contention,
            "process_rss": {
                "before_bytes": rss_before,
                "after_measurement_bytes": process.memory_info().rss,
            },
        }
    except BaseException as exc:
        result = {
            "version": VERSION,
            "started_at": started_at,
            "finished_at": timestamp(),
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}"[:2048],
        }
        raise
    finally:
        if store is not None:
            store.close()
        if fixture.exists():
            shutil.rmtree(fixture)
        result["cleanup"] = {
            "fixture_owned": True,
            "fixture_removed": not fixture.exists(),
        }
        (evidence_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--devices", type=int, default=10_000)
    parser.add_argument("--audit-rows", type=int, default=1_000_000)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--target-seconds", type=float, default=1.0)
    args = parser.parse_args()
    result = run_load(
        args.evidence_dir,
        devices=args.devices,
        audit_rows=args.audit_rows,
        iterations=args.iterations,
        target_seconds=args.target_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if (
        not result["device_metadata"]["target_met"]
        or not result["audit_search"]["target_met"]
        or not result["sqlite_writer_contention"]["writer_was_blocked"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
