"""Run bounded Gateway management acceptance scenarios and write local evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

Scenario = Literal["multi-agent", "upgrade", "restore", "soak"]


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_credential(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("credential reference must be a regular local file")
    data = json.loads(path.read_text(encoding="utf-8"))
    cookie = str(data.get("session_cookie") or "")
    csrf = str(data.get("csrf_token") or "")
    if not cookie or not csrf:
        raise ValueError("credential reference must contain session_cookie and csrf_token")
    return {"session_cookie": cookie, "csrf_token": csrf}


def collect_snapshot(
    client: httpx.Client,
    scenario: Scenario,
    *,
    duration_seconds: int = 0,
) -> dict[str, Any]:
    status = client.get("/api/v1/management/status")
    status.raise_for_status()
    evidence: dict[str, Any] = {"status": status.json()}
    if scenario == "multi-agent":
        devices = client.get("/api/v1/management/devices?limit=100")
        devices.raise_for_status()
        evidence["devices"] = devices.json()
    elif scenario == "upgrade":
        update = client.get("/api/v1/management/updates/status")
        update.raise_for_status()
        evidence["update"] = update.json()
    elif scenario == "restore":
        backups = client.get("/api/v1/management/backups")
        backups.raise_for_status()
        evidence["backups"] = backups.json()
        previews = []
        for backup in backups.json()[:10]:
            preview = client.get(
                f"/api/v1/management/backups/{backup['id']}/restore-preview"
            )
            preview.raise_for_status()
            previews.append(preview.json())
        evidence["restore_previews"] = previews
    else:
        samples = []
        deadline = time.monotonic() + duration_seconds
        while True:
            response = client.get("/api/v1/management/status")
            response.raise_for_status()
            samples.append(
                {
                    "observed_at": time.time(),
                    "ready": response.json()["ready"],
                    "database": response.json()["database"],
                    "connected_devices": response.json()["connected_devices"],
                }
            )
            if time.monotonic() >= deadline:
                break
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        evidence["samples"] = samples
    return evidence


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _candidate_hashes(paths: list[Path]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"candidate evidence is not a regular file: {path}")
        result.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _digest(path),
            }
        )
    return result


def validate_snapshot(
    evidence: dict[str, Any],
    *,
    expected_version: str | None = None,
    min_devices: int = 0,
) -> None:
    status = evidence["status"]
    if expected_version is not None and status.get("version") != expected_version:
        raise ValueError("Gateway version differs from the pinned acceptance candidate")
    if status.get("database") != "ready":
        raise ValueError("Gateway database is not ready")
    if min_devices:
        devices = evidence.get("devices")
        if not isinstance(devices, dict) or not isinstance(devices.get("items"), list):
            raise ValueError("multi-agent evidence does not contain a device page")
        ids = [str(item.get("id", "")) for item in devices["items"]]
        if len(ids) < min_devices or len(set(ids)) != len(ids) or any(not value for value in ids):
            raise ValueError("multi-agent acceptance did not observe the required unique devices")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=["multi-agent", "upgrade", "restore", "soak"],
        required=True,
    )
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--duration-seconds", type=int, default=8 * 60 * 60)
    parser.add_argument("--expected-version")
    parser.add_argument("--min-devices", type=int, default=0)
    parser.add_argument("--candidate-file", action="append", type=Path, default=[])
    parser.add_argument("--topology-file", type=Path)
    args = parser.parse_args()
    if args.evidence_dir.exists():
        raise SystemExit("evidence-dir must be a new directory")
    if args.duration_seconds < 0:
        raise SystemExit("duration-seconds must be non-negative")
    if args.min_devices < 0 or args.min_devices > 100:
        raise SystemExit("min-devices must be between 0 and 100")
    credential = load_credential(args.credential_file)
    if args.ca_file is not None and (args.ca_file.is_symlink() or not args.ca_file.is_file()):
        raise SystemExit("ca-file must be a regular local file")
    if args.topology_file is not None and (
        args.topology_file.is_symlink() or not args.topology_file.is_file()
    ):
        raise SystemExit("topology-file must be a regular local file")
    candidate_hashes = _candidate_hashes(args.candidate_file)
    credential_sha256 = _digest(args.credential_file)
    ca_sha256 = _digest(args.ca_file) if args.ca_file else None
    topology_sha256 = _digest(args.topology_file) if args.topology_file else None
    args.evidence_dir.mkdir(parents=True)
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    verify: bool | str = str(args.ca_file) if args.ca_file else True
    with httpx.Client(
        base_url=args.gateway.rstrip("/"),
        verify=verify,
        trust_env=False,
        follow_redirects=False,
        timeout=10,
        cookies={"racp_console": credential["session_cookie"]},
        headers={"X-CSRF-Token": credential["csrf_token"]},
    ) as client:
        evidence = collect_snapshot(
            client,
            args.scenario,
            duration_seconds=args.duration_seconds,
        )
    validate_snapshot(
        evidence,
        expected_version=args.expected_version,
        min_devices=args.min_devices if args.scenario == "multi-agent" else 0,
    )
    finished_at = _utc_now()
    result = {
        "scenario": args.scenario,
        "gateway": args.gateway,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
        "host": {"computer_name": os.environ.get("COMPUTERNAME", "unknown")},
        "credential_reference_sha256": credential_sha256,
        "ca_sha256": ca_sha256,
        "candidate_files": candidate_hashes,
        "topology_sha256": topology_sha256,
        "expected_version": args.expected_version,
        "cleanup": {
            "fixture_owned_by_cli": False,
            "status": "not_required",
            "reason": "acceptance CLI is read-only and does not create Gateway/Agent fixtures",
        },
        "evidence": evidence,
    }
    (args.evidence_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"scenario": args.scenario, "evidence": str(args.evidence_dir)}))


if __name__ == "__main__":
    main()
