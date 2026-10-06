"""Exercise one explicitly selected remote Windows PC using test-owned files only."""

import argparse
import hashlib
import json
import platform
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from racp_sdk.security import SecretStore, tls_context


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab-dir", required=True, type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    owner = SecretStore(args.lab_dir / "owner.bin").load()
    run_id = uuid.uuid4().hex
    folder = "racp-acceptance-" + run_id
    evidence: dict[str, Any] = {
        "started_utc": datetime.now(UTC).isoformat(),
        "device_id": args.device,
        "controller_hostname": platform.node(),
        "fixture_folder": folder,
        "steps": [],
    }
    with httpx.Client(
        base_url=owner["gateway"],
        verify=tls_context(args.lab_dir / "ca.pem"),
        headers={"Authorization": "Bearer " + owner["token"]},
        trust_env=False,
        follow_redirects=False,
        timeout=40,
    ) as http:

        def call(
            name: str,
            payload: dict[str, Any],
            *,
            key: str | None = None,
            job: bool = False,
        ) -> dict[str, Any]:
            request: dict[str, Any] = {
                "device_id": args.device,
                "operation": name,
                "payload": payload,
                "execution_profile_id": "standard",
                "idempotency_key": key or uuid.uuid4().hex,
            }
            if job:
                request.update(execution_mode="job", timeout_ms=60000)
            response = http.post("/api/v1/operations", json=request)
            approval = None
            if response.status_code == 409:
                details = response.json().get("error", {}).get("details", {})
                approval = details.get("approval_id")
                if approval:
                    approved = http.post("/api/v1/approvals/" + approval + "/approve")
                    approved.raise_for_status()
                    request["approval_id"] = approval
                    response = http.post("/api/v1/operations", json=request)
            response.raise_for_status()
            result = response.json()
            if not job and result.get("state") != "SUCCEEDED":
                raise RuntimeError("Remote operation did not succeed: " + name)
            evidence["steps"].append(
                {
                    "operation": name,
                    "operation_id": result["operation_id"],
                    "state": result["state"],
                    "approval_id": approval,
                    "result": result.get("result"),
                }
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return result if job else dict(result["result"])

        device = http.get("/api/v1/devices/" + args.device)
        device.raise_for_status()
        info = device.json()["info"]
        if info.get("status") != "ONLINE" or info.get("platform") != "Windows":
            raise RuntimeError("Selected Windows PC is not online")
        evidence["agent_boot_id"] = info["agent_boot_id"]
        evidence["connection_epoch"] = device.json()["epoch"]
        evidence["execution_identity"] = info["execution_identity"]
        call("filesystem.mkdir", {"path": folder})
        content = "RACP 원격 PC 인수시험\n" + run_id + "\n"
        source = folder + "/자료.txt"
        call("filesystem.write", {"path": source, "content": content})
        read = call("filesystem.read", {"path": source})
        if read.get("text") != content:
            raise RuntimeError("Remote UTF-8 file content differs")
        digest = call("filesystem.hash", {"path": source})
        if digest.get("sha256") != hashlib.sha256(content.encode()).hexdigest():
            raise RuntimeError("Remote file SHA-256 differs")
        call("filesystem.copy", {"source": source, "destination": folder + "/copy.txt"})
        binary = call("filesystem.read", {"path": source, "binary": True})
        transferred = http.get("/api/v1/artifacts/" + binary["artifact_id"] + "/content")
        transferred.raise_for_status()
        if transferred.content != content.encode("utf-8"):
            raise RuntimeError("Authenticated remote Artifact content differs")
        evidence["artifact_download_sha256"] = hashlib.sha256(transferred.content).hexdigest()
        shell = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
            f"$p = '{folder}/counter.txt'; "
            "$n = if (Test-Path -LiteralPath $p) "
            "{ [int](Get-Content -LiteralPath $p) } else { 0 }; "
            "[System.IO.File]::WriteAllText((Join-Path (Get-Location) $p), [string]($n + 1)); "
            "Write-Output ([Environment]::MachineName); Write-Output (Get-Location).Path"
        )
        shell_payload = {
            "argv": ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", shell]
        }
        first = call("shell.exec", shell_payload, key=run_id + "-counter")
        second = call("shell.exec", shell_payload, key=run_id + "-counter")
        if first != second:
            raise RuntimeError("Remote idempotent replay changed its result")
        output = first["stdout"].strip().splitlines()
        if not output or output[0].casefold() == platform.node().casefold():
            raise RuntimeError("Remote host must differ from the controller")
        evidence["remote_hostname"] = output[0]
        evidence["remote_cwd"] = output[-1]
        if call("filesystem.read", {"path": folder + "/counter.txt"}).get("text") != "1":
            raise RuntimeError("Remote command executed more than once")
        call("filesystem.write", {"path": folder + "/job.pid", "content": "pending"})
        job_script = (
            f"[System.IO.File]::WriteAllText((Join-Path (Get-Location) '{folder}/job.pid'), "
            "[string]$PID); Start-Sleep -Seconds 45"
        )
        job = call(
            "shell.exec",
            {"argv": ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", job_script]},
            job=True,
        )
        try:
            owned_pid = "pending"
            for _ in range(100):
                owned_pid = call("filesystem.read", {"path": folder + "/job.pid"})["text"]
                if owned_pid.isdigit():
                    break
                time.sleep(0.1)
            if not owned_pid.isdigit():
                raise RuntimeError("Owned remote Job did not publish its PID")
        finally:
            cancelled = http.post("/api/v1/operations/" + job["operation_id"] + "/cancel")
            cancelled.raise_for_status()
        outcome: dict[str, Any] = {}
        for _ in range(100):
            response = http.get("/api/v1/operations/" + job["operation_id"])
            response.raise_for_status()
            outcome = response.json()
            if outcome["state"] in {"CANCELLED", "FAILED", "UNKNOWN", "TIMED_OUT", "SUCCEEDED"}:
                break
            time.sleep(0.1)
        if outcome["state"] != "CANCELLED" or outcome["result"]["cleanup_status"] != "complete":
            raise RuntimeError("Owned remote Job cancellation is unconfirmed")
        evidence["cancelled_job"] = outcome
        checked = call(
            "shell.exec",
            {
                "argv": [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"if (Get-Process -Id {owned_pid} -ErrorAction SilentlyContinue) "
                    "{ exit 3 }; Write-Output 'OWNED-JOB-GONE'",
                ]
            },
        )
        if checked["exit_code"] != 0 or checked["stdout"].strip() != "OWNED-JOB-GONE":
            raise RuntimeError("Owned remote Job process still exists")
        opened = call("terminal.open", {"argv": ["powershell.exe", "-NoLogo", "-NoProfile"]})
        handle = opened["handle_id"]
        try:
            marker = "RACP-TERMINAL-" + run_id
            call(
                "terminal.write",
                {
                    "handle_id": handle,
                    "data": "Write-Output ('RACP-' + 'TERMINAL-' + '" + run_id + "')\r",
                },
            )
            cursor, text = "0", ""
            for _ in range(60):
                result = call(
                    "terminal.read", {"handle_id": handle, "cursor": cursor, "wait_ms": 200}
                )
                cursor = result["next_cursor"]
                text += result["data"]
                if marker in text:
                    break
                time.sleep(0.05)
            if marker not in text:
                raise RuntimeError("Remote persistent terminal did not execute the marker")
        finally:
            closed = call("terminal.close", {"handle_id": handle})
            if closed["cleanup_status"] != "complete":
                raise RuntimeError("Owned remote terminal cleanup is unconfirmed")
        evidence["status"] = "PASS"
        evidence["completed_utc"] = datetime.now(UTC).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "status": "PASS",
                "device": args.device,
                "remote_hostname": evidence["remote_hostname"],
                "fixture_folder": folder,
                "evidence": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
