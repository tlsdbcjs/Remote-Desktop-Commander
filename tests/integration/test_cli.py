import asyncio
import json
import subprocess
import sys
from typing import Any

from racp_sdk.security import SecretStore


async def test_authenticated_cli_lists_and_executes_without_printing_secrets(
    live: dict[str, Any],
) -> None:
    store = live["workspace"] / "owner.bin"
    SecretStore(store).save({"token": live["owner"], "gateway": live["url"]})
    prefix = [
        sys.executable,
        "-m",
        "racp_cli.main",
        "--gateway",
        live["url"],
        "--owner-store",
        str(store),
        "--json",
    ]
    listed = await asyncio.to_thread(
        subprocess.run,
        [*prefix, "device", "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["items"][0]["id"] == live["device_id"]
    assert live["owner"] not in listed.stdout and live["credential"] not in listed.stdout
    diagnosed = await asyncio.to_thread(
        subprocess.run,
        [*prefix, "doctor"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert diagnosed.returncode == 0, diagnosed.stderr
    diagnostic = json.loads(diagnosed.stdout)["diagnostics"]
    assert diagnostic["devices"][0]["id"] == live["device_id"]
    assert diagnostic["gateway"]["mcp"]["authentication"] == "owner_bearer"
    assert live["owner"] not in diagnosed.stdout and live["credential"] not in diagnosed.stdout
    executed = await asyncio.to_thread(
        subprocess.run,
        [
            *prefix,
            "shell",
            live["device_id"],
            "--key",
            "cli-command",
            "--profile",
            "trusted_personal",
            "--",
            sys.executable,
            "-c",
            "import sys; print('cli'); sys.exit(7)",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert executed.returncode == 0, executed.stderr
    assert json.loads(executed.stdout)["result"]["exit_code"] == 7
    denied = await asyncio.to_thread(
        subprocess.run,
        [
            *prefix,
            "shell",
            live["device_id"],
            "--key",
            "cli-denied",
            "--",
            sys.executable,
            "--version",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert denied.returncode == 3


async def test_cli_filesystem_and_artifact_download(live: dict[str, Any]) -> None:
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
    write = await asyncio.to_thread(
        subprocess.run,
        [
            *prefix,
            "fs",
            "write",
            live["device_id"],
            "cli.txt",
            "--content",
            "cli-text",
            "--key",
            "cli-fs-create",
            "--profile",
            "trusted_personal",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert write.returncode == 0, write.stderr
    read = await asyncio.to_thread(
        subprocess.run,
        [*prefix, "fs", "read", live["device_id"], "cli.txt"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert read.returncode == 0 and json.loads(read.stdout)["result"]["text"] == "cli-text"
    binary = await asyncio.to_thread(
        subprocess.run,
        [*prefix, "fs", "read", live["device_id"], "cli.txt", "--binary"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert binary.returncode == 0, binary.stderr
    artifact_id = json.loads(binary.stdout)["result"]["artifact_id"]
    output = live["workspace"].parent / "download.txt"
    downloaded = await asyncio.to_thread(
        subprocess.run,
        [*prefix, "artifact", "download", artifact_id, str(output)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert downloaded.returncode == 0, downloaded.stderr
    assert await asyncio.to_thread(output.read_bytes) == b"cli-text"
