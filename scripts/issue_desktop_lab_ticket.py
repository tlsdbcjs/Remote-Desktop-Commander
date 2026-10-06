"""Issue a short-lived client ticket using the existing protected two-PC lab owner."""

import argparse
import json
import shutil
from pathlib import Path

import httpx
from racp_sdk.connection_file import ConnectionFile
from racp_sdk.security import SecretStore, tls_context


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--name", default="Windows PC 192.168.29.141")
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    owner = SecretStore(args.lab_dir / "owner.bin").load()
    with httpx.Client(
        base_url=owner["gateway"],
        verify=tls_context(args.lab_dir / "ca.pem"),
        trust_env=False,
        follow_redirects=False,
        timeout=15,
        headers={"Authorization": "Bearer " + owner["token"]},
    ) as http:
        response = http.post(
            "/api/v1/enrollment-tokens",
            json={"name": args.name, "include_connection_file": True},
        )
        response.raise_for_status()
        value = response.json()
    try:
        connection = ConnectionFile.model_validate_json(json.dumps(value["connection_file"]))
        if connection.gateway != owner["gateway"].rstrip("/") or connection.token != value["token"]:
            raise ValueError("Connection identity mismatch")
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
        f"Gateway: {owner['gateway']}\nCA: ca.pem in this folder\n"
        f"Enrollment token: {value['token']}\nExpires UTC: {expires.isoformat()}\n"
        "One use, 10 minutes. Select a local workspace and profile; register, then start Agent.\n"
        "Delete this connection.txt after successful registration. Keep ca.pem in a stable path.\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "expires_utc": expires.isoformat()}))


if __name__ == "__main__":
    main()
