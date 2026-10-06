import asyncio
import json
import subprocess
import sys
from typing import Any

from racp_sdk.security import SecretStore


async def test_cli_binary_upload_write_and_terminal_without_secrets(live: dict[str, Any]) -> None:
    store = live["workspace"].parent / "cli-owner.bin"
    SecretStore(store).save({"token": live["owner"], "gateway": live["url"]})
    prefix = [
        sys.executable,
        "-m",
        "racp_cli.main",
        "--gateway",
        live["url"],
        "--owner-store",
        str(store),
    ]

    async def cli(*argv: str) -> dict[str, Any]:
        completed = await asyncio.to_thread(
            subprocess.run,
            [*prefix, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert completed.returncode == 0, completed.stderr
        assert live["owner"] not in completed.stdout + completed.stderr
        assert live["credential"] not in completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    raw = bytes(range(256)) * 1024
    source = live["workspace"].parent / "input.bin"
    source.write_bytes(raw)
    artifact = await cli("artifact", "upload", live["device_id"], str(source))
    assert "credential" not in artifact and artifact["state"] == "READY"
    write = await cli(
        "fs",
        "write",
        live["device_id"],
        "cli-binary.bin",
        "--artifact-id",
        artifact["id"],
        "--key",
        "cli-binary",
        "--profile",
        "trusted_personal",
    )
    assert write["state"] == "SUCCEEDED"
    assert (live["workspace"] / "cli-binary.bin").read_bytes() == raw
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await cli(
        "terminal",
        "open",
        live["device_id"],
        "--argv",
        json.dumps([python, "-q", "-i"]),
        "--key",
        "cli-terminal",
        "--profile",
        "trusted_personal",
    )
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    read = await cli("terminal", "read", live["device_id"], handle, "--wait-ms", "1000")
    assert ">>>" in read["result"]["data"]
    closed = await cli(
        "terminal",
        "close",
        live["device_id"],
        handle,
        "--key",
        "cli-close",
        "--profile",
        "trusted_personal",
    )
    assert closed["result"]["state"] == "CLOSED"
