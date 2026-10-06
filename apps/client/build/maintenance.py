"""Installer-owned guard: stop the saved user Agent, back up state, preserve user data."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import psutil
from racp_agent.background import status, stop
from racp_agent.instance_lock import InstanceLock
from racp_agent.settings import local_path
from racp_sdk.security import SecretStore, digest


def no_gui_running(install_dir: Path, executable_name: str) -> None:
    expected = (install_dir / executable_name).resolve()
    for process in psutil.process_iter(["name"]):
        if (
            not isinstance(process.info["name"], str)
            or process.info["name"].casefold() != executable_name.casefold()
        ):
            continue
        try:
            executable = Path(process.exe()).resolve()
        except psutil.NoSuchProcess:
            continue
        if executable == expected:
            raise RuntimeError(
                "Close this installed client's GUI using full exit before maintenance"
            )


def backup_state(root: Path) -> Path | None:
    if not root.exists():
        return None
    output = local_path(root / "backups" / ("before-maintenance-" + uuid.uuid4().hex))
    output.mkdir(parents=True, mode=0o700)
    records = []
    for current, directories, files in os.walk(root, followlinks=False):
        parent = local_path(Path(current))
        if parent == root:
            directories[:] = [name for name in directories if name.casefold() != "backups"]
        for name in directories:
            local_path(parent / name)
        for name in files:
            if name.endswith(".lock"):
                continue  # Lifetime locks are process state, not a restorable artifact.
            source = local_path(parent / name)
            relative = source.relative_to(root)
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(source, destination)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            if hashlib.sha256(destination.read_bytes()).hexdigest() != before:
                raise RuntimeError("State backup verification failed")
            records.append({"file": relative.as_posix(), "sha256": before})
    (output / "backup-manifest.json").write_text(
        json.dumps({"version": 1, "kind": "same-user-agent-state", "files": records}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return output


def remove_own_login(name: str, executable: Path) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Startup registry maintenance requires Windows")
    import winreg

    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
        ) as key:
            command, kind = winreg.QueryValueEx(key, name)
            expected = '"' + str(executable) + '" racp-background-agent'
            if kind != winreg.REG_SZ or command.casefold() != expected.casefold():
                raise RuntimeError("Startup registration belongs to a different installation")
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass
    return True


async def prepare(
    install_dir: Path, root: Path, executable_name: str, mode: str, login_name: str
) -> dict[str, Any]:
    await asyncio.to_thread(no_gui_running, install_dir, executable_name)
    credentials = local_path(root / "credential.bin")
    stopped = await stop(credentials)
    if stopped["state"] != "STOPPED" or stopped.get("cleanup_status") == "unknown":
        raise RuntimeError("Agent cleanup is not confirmed")
    if credentials.exists():
        value = SecretStore(credentials).load()
        # Hold the same Device lock while copying the closed state.
        lock = InstanceLock(root / ("agent-" + digest(value["device_id"]) + ".lock"))
        with lock:
            backup = await asyncio.to_thread(backup_state, root)
    else:
        backup = await asyncio.to_thread(backup_state, root)
    if (await status(credentials))["state"] != "STOPPED":
        raise RuntimeError("Agent restarted during maintenance")
    removed = False
    if mode == "uninstall":
        removed = await asyncio.to_thread(
            remove_own_login, login_name, install_dir / executable_name
        )
    return {
        "agent": "STOPPED",
        "data": "preserved",
        "backup": str(backup) if backup else None,
        "startup_removed": removed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--executable-name", required=True)
    parser.add_argument("--login-name", required=True)
    parser.add_argument("--mode", choices=["upgrade", "uninstall"], required=True)
    args = parser.parse_args()
    try:
        if os.name != "nt" or Path(args.executable_name).name != args.executable_name:
            raise ValueError("Maintenance requires the Windows client executable name")
        result = asyncio.run(
            prepare(
                local_path(args.install_dir.absolute()),
                local_path(args.state_dir.absolute()),
                args.executable_name,
                args.mode,
                args.login_name,
            )
        )
        print(json.dumps(result))
    except Exception:
        print("Maintenance not confirmed. Use full exit and inspect local state before retrying.")
        raise SystemExit(4) from None


if __name__ == "__main__":
    main()
