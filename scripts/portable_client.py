"""Portable test Agent launcher. No machine credentials or owner tokens are bundled."""

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path

from racp_agent.background import launch_arguments, start, status, stop
from racp_agent.connect import enroll
from racp_agent.main import parser as agent_parser
from racp_domain.models import RACPError


def main() -> None:
    parser = argparse.ArgumentParser(description="RACP portable Windows test client")
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--token-stdin", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "browsers")
    state_dir = (args.state_dir or root / "state").absolute()
    credential_store = state_dir / "credential.bin"
    try:
        if args.action == "start":
            if not credential_store.exists():
                config = json.loads((root / "client-config.json").read_text(encoding="utf-8"))
                workspace = (args.workspace or root / "workspace").absolute()
                workspace.mkdir(exist_ok=True)
                ticket = root / "enrollment.txt"
                import sys

                secret = (
                    sys.stdin.readline(130).strip()
                    if args.token_stdin
                    else (
                        ticket.read_text(encoding="utf-8").strip()
                        if ticket.exists()
                        else getpass.getpass("One-use PC enrollment token: ")
                    )
                )
                print("Connecting this PC to " + config["gateway"])
                print("Test workspace: " + str(workspace))
                enroll(
                    config["gateway"],
                    workspace,
                    state_dir,
                    secret,
                    profile=config["profile"],
                    ca_file=root / "ca.pem",
                )
                ticket.unlink(missing_ok=True)
            options = agent_parser().parse_args(["--credentials", str(credential_store)])
            result = asyncio.run(start(credential_store, options, launch_arguments(options)))
        elif args.action == "status":
            result = asyncio.run(status(credential_store))
        else:
            result = asyncio.run(stop(credential_store))
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, RACPError) as error:
        print(
            "Client request failed ("
            + type(error).__name__
            + "). Check the Gateway, ticket and local log."
        )
        raise SystemExit(4) from None


if __name__ == "__main__":
    main()
