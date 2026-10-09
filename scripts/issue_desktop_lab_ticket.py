"""Issue a short-lived client ticket using the existing protected two-PC lab owner."""

import argparse
import json
import shutil
from pathlib import Path

import httpx
from racp_sdk.connection_file import ConnectionFile
from racp_sdk.security import SecretStore, tls_context


def request_connection(lab_dir: Path, name: str) -> ConnectionFile:
    """Request a self-contained document using the host's protected owner identity."""
    owner = SecretStore(lab_dir / "owner.bin").load()
    with httpx.Client(
        base_url=owner["gateway"],
        verify=tls_context(lab_dir / "ca.pem"),
        trust_env=False,
        follow_redirects=False,
        timeout=15,
        headers={"Authorization": "Bearer " + owner["token"]},
    ) as http:
        http.get("/readyz").raise_for_status()
        response = http.post(
            "/api/v1/enrollment-tokens",
            json={"name": name, "include_connection_file": True},
        )
        response.raise_for_status()
        value = response.json()
    connection = ConnectionFile.model_validate_json(json.dumps(value["connection_file"]))
    if connection.gateway != owner["gateway"].rstrip("/") or connection.token != value["token"]:
        raise ValueError("Connection identity mismatch")
    if connection.ca_pem is None:
        connection = ConnectionFile.model_validate(
            {**connection.model_dump(), "ca_pem": (lab_dir / "ca.pem").read_text(encoding="utf-8")}
        )
    return connection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--name", default="Windows PC 192.168.29.141")
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    try:
        connection = request_connection(args.lab_dir, args.name)
    except (ValueError, KeyError):
        raise SystemExit("Gateway returned an invalid connection document") from None
    expires = connection.expires_at
    shutil.copy2(args.lab_dir / "ca.pem", output / "ca.pem")
    (output / "RACP-connection.racp").write_text(
        connection.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "connection.txt").write_text(
        "RACP Client: Gateway / CA / enrollment token\n"
        f"Gateway: {connection.gateway}\nCA: ca.pem in this folder\n"
        f"Enrollment token: {connection.token}\nExpires UTC: {expires.isoformat()}\n"
        "One use, 10 minutes. Select a local workspace and profile; register, then start Agent.\n"
        "Delete this connection.txt after successful registration. Keep ca.pem in a stable path.\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "expires_utc": expires.isoformat()}))


if __name__ == "__main__":
    main()
