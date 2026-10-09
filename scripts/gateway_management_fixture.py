"""Create an isolated local Gateway management acceptance fixture.

The fixture owns only the requested new state directory and writes the browser
session secret to a separate credential reference supplied by the caller.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

from racp_domain.version import VERSION
from racp_gateway.config import GatewayConfig, write_gateway_config
from racp_gateway.console_auth import ConsoleAuth
from racp_gateway.store import GatewayStore
from racp_sdk.security import digest


@dataclass(frozen=True)
class FixtureReceipt:
    state_dir: Path
    config_path: Path
    credential_file: Path
    topology_file: Path
    device_count: int


def prepare_fixture(
    state_dir: Path,
    credential_file: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8877,
    device_count: int = 50,
) -> FixtureReceipt:
    if state_dir.exists():
        raise FileExistsError("acceptance fixture state directory must be new")
    if credential_file.exists() or credential_file.is_symlink():
        raise FileExistsError("acceptance credential reference must be new")
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("acceptance fixture is loopback-only")
    if not 1 <= port <= 65535:
        raise ValueError("acceptance fixture port is invalid")
    if not 1 <= device_count <= 100:
        raise ValueError("acceptance fixture device count must be between 1 and 100")

    config_path = state_dir / "config" / "gateway.json"
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_acceptance_fixture",
            state_root=str(state_dir.resolve()),
            bind_address=host,
            public_origin=f"http://{host}:{port}",
            port=port,
        ),
    )
    store = GatewayStore(state_dir / "gateway.db")
    store.initialize(digest("isolated-acceptance-owner"))
    device_ids: list[str] = []
    try:
        for index in range(device_count):
            enrolled = store.enroll(store.enrollment(f"synthetic-{index:03d}"))
            device_ids.append(enrolled["device_id"])
            store.connected(
                enrolled["device_id"],
                {
                    "status": "ONLINE",
                    "platform": "synthetic-windows-x64",
                    "agent_version": VERSION,
                },
            )
            store.device_status(enrolled["device_id"], "ONLINE")
        credential, session = ConsoleAuth(store).create_session("owner_local")
    finally:
        store.close()

    credential_file.parent.mkdir(parents=True, exist_ok=True)
    credential_file.write_text(
        json.dumps(
            {"session_cookie": credential, "csrf_token": session.csrf_token},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(credential_file, 0o600)
    except OSError:
        pass
    topology_file = state_dir / "acceptance-topology.json"
    topology_file.write_text(
        json.dumps(
            {
                "version": VERSION,
                "gateway": f"http://{host}:{port}",
                "fixture_owned": True,
                "synthetic_devices": device_count,
                "device_ids": device_ids,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return FixtureReceipt(
        state_dir=state_dir,
        config_path=config_path,
        credential_file=credential_file,
        topology_file=topology_file,
        device_count=device_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--devices", type=int, default=50)
    args = parser.parse_args()
    receipt = prepare_fixture(
        args.state_dir,
        args.credential_file,
        host=args.host,
        port=args.port,
        device_count=args.devices,
    )
    print(
        json.dumps(
            {
                "config": str(receipt.config_path),
                "topology": str(receipt.topology_file),
                "credential_reference": str(receipt.credential_file),
                "devices": receipt.device_count,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
