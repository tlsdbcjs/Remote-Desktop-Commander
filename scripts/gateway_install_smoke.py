"""Validate an installed Gateway service without mutating unrelated machine state."""

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    query = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Service -Filter \"Name='RACP Gateway'\" | "
            "Select-Object Name,State,StartMode,StartName,PathName | ConvertTo-Json -Compress",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    service = json.loads(query.stdout)
    if service.get("Name") != "RACP Gateway":
        raise SystemExit("RACP Gateway service is not installed")
    if service.get("State") != "Running" or service.get("StartMode") != "Auto":
        raise SystemExit("RACP Gateway service is not running with automatic start")
    if service.get("StartName") != r"NT SERVICE\RACP Gateway":
        raise SystemExit("RACP Gateway service identity differs from the deployment contract")
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(
            {
                "version": args.expected_version,
                "service": service,
                "result": "pass",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
