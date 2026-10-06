"""Native Windows install/upgrade/uninstall against isolated test-owned app identities."""

import argparse
import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from racp_agent.connect import credential_document
from racp_agent.settings import AgentSettings
from racp_gateway.app import create_app
from racp_gateway.store import GatewayStore
from racp_sdk.security import SecretStore, digest, token


async def run(fixture_path: Path, resume: bool = False) -> None:
    metadata = json.loads(await asyncio.to_thread(fixture_path.read_text, encoding="utf-8"))
    identifier = metadata["id"]
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Expected an isolated fixture identifier")
    package = "racp-install-acceptance-" + identifier
    if metadata["package"] != package or metadata["app_id"] != "app.racp.acceptance." + identifier:
        raise ValueError("Fixture must have its own package and registry identity")
    root = Path(".racp") / ("installer-acceptance-" + identifier)
    root = await asyncio.to_thread(root.resolve)
    root.mkdir(parents=True, exist_ok=resume)
    installed = root / "program"
    state = Path(os.environ["APPDATA"]) / package / "agent"
    if state.parent.exists() and not resume:
        raise ValueError("Fixture app data already exists; refusing to touch it")
    attempt = uuid.uuid4().hex
    workspace = root / ("workspace-" + attempt)
    workspace.mkdir()
    executable = installed / (metadata["product"] + ".exe")
    report: dict[str, Any] = {"fixture": str(fixture_path), "steps": []}
    owner = token()
    gateway_state = root / ("gateway-" + attempt)
    store = GatewayStore(gateway_state / "gateway.db")
    store.initialize(digest(owner))
    device = store.enroll(store.enrollment("Installer acceptance fixture"))
    store.close()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    gateway = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(gateway_state, trusted_personal=True), log_level="error", lifespan="on"
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    credentials = state / "credential.bin"
    python = installed / "resources/agent/runtime/python.exe"

    async def command(
        argv: list[str], expected: int = 0, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        result = await asyncio.to_thread(
            subprocess.run,
            argv,
            capture_output=True,
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=environment,
        )
        if result.returncode != expected:
            raise RuntimeError(f"Fixture command returned {result.returncode}; expected {expected}")
        return result

    async def control(action: str) -> dict[str, Any]:
        result = await asyncio.to_thread(
            subprocess.run,
            [str(python), "-I", "-m", "racp_agent.desktop_control", "--state-dir", str(state)],
            input=json.dumps({"action": action}).encode(),
            capture_output=True,
            timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError("Installed fixture Agent control failed")
        return json.loads(result.stdout)["result"]

    async with httpx.AsyncClient(
        base_url=gateway, timeout=30, headers={"Authorization": "Bearer " + owner}
    ) as http:

        async def online(previous_epoch: int = -1) -> dict[str, Any]:
            for _ in range(200):
                observed = (await http.get("/api/v1/devices/" + device["device_id"])).json()
                if (
                    observed["info"].get("status") == "ONLINE"
                    and observed["epoch"] > previous_epoch
                ):
                    return observed
                await asyncio.sleep(0.05)
            raise RuntimeError("Installed fixture Agent did not connect")

        async def execute() -> dict[str, Any]:
            code = (
                "$p='counter.txt'; $n = if (Test-Path -LiteralPath $p) "
                "{ [int](Get-Content -LiteralPath $p) } else { 0 }; "
                "[System.IO.File]::WriteAllText((Join-Path (Get-Location) $p), [string]($n+1)); "
                "Write-Output 'INSTALLER-ONCE'"
            )
            response = await http.post(
                "/api/v1/operations",
                json={
                    "device_id": device["device_id"],
                    "operation": "shell.exec",
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": attempt,
                    "payload": {
                        "argv": [
                            "powershell.exe",
                            "-NoProfile",
                            "-NonInteractive",
                            "-Command",
                            code,
                        ]
                    },
                },
            )
            response.raise_for_status()
            result = response.json()
            if result["state"] != "SUCCEEDED":
                raise RuntimeError("Installed fixture command did not succeed")
            return result

        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            first, second = metadata["versions"]
            recovery_environment = None
            if resume:
                # A test-only recovery from the previously installed stock
                # cross-volume uninstaller. The actual upgrade below uses the
                # normal C: temp directory against the fixed E: installation.
                recovery_temp = root / "recovery-temp"
                recovery_temp.mkdir(exist_ok=True)
                recovery_environment = dict(
                    os.environ, TEMP=str(recovery_temp), TMP=str(recovery_temp)
                )
            if not (resume and executable.is_file() and python.is_file()):
                await command(
                    [first["installer"], "/S", "/currentuser", "/D=" + str(installed)],
                    environment=recovery_environment,
                )
            if not executable.is_file() or not python.is_file():
                raise RuntimeError("Installer did not produce its standalone runtime")
            import win32api

            def installed_version() -> str:
                version = win32api.GetFileVersionInfo(str(executable), "\\")
                return ".".join(
                    str(part)
                    for part in (
                        version["FileVersionMS"] >> 16,
                        version["FileVersionMS"] & 65535,
                        version["FileVersionLS"] >> 16,
                    )
                )

            if installed_version() != first["version"]:
                raise RuntimeError("Initial installed executable version mismatch")
            report["steps"].append(
                "fixture recovery install" if resume else "clean fixture install"
            )
            state.mkdir(parents=True, exist_ok=True)
            settings = AgentSettings(
                gateway=gateway,
                device_id=device["device_id"],
                workspace=workspace,
                data_dir=state / "data",
                profile="trusted_personal",
            )
            SecretStore(credentials).save(credential_document(settings, device["credential"]))
            credential_hash = hashlib.sha256(credentials.read_bytes()).hexdigest()
            await control("start")
            initial_connection = await online()
            original = await execute()
            report["steps"].append("installed Agent executes real remote operation")
            await command([second["installer"], "/S", "/currentuser", "/D=" + str(installed)])
            if installed_version() != second["version"]:
                raise RuntimeError("Upgrade did not switch the executable version")
            if list(root.glob("nsi*.tmp")):
                raise RuntimeError("Upgrade left a temporary old installation")
            if (await control("status"))["state"] != "STOPPED":
                raise RuntimeError("Upgrade did not stop the original Agent")
            if hashlib.sha256(credentials.read_bytes()).hexdigest() != credential_hash:
                raise RuntimeError("Upgrade modified the saved credential")
            backups = list((state / "backups").glob("*/backup-manifest.json"))
            if not backups:
                raise RuntimeError("Upgrade did not create a verified state backup")
            for manifest_path in backups:
                manifest = json.loads(manifest_path.read_text())
                for item in manifest["files"]:
                    if (
                        hashlib.sha256(
                            (manifest_path.parent / item["file"]).read_bytes()
                        ).hexdigest()
                        != item["sha256"]
                    ):
                        raise RuntimeError("Fixture backup hash mismatch")
            report["steps"].append("upgrade stops Agent, preserves credential and verifies backup")
            with sqlite3.connect(
                (state / "data/execution.db").as_uri() + "?mode=ro", uri=True
            ) as db:
                retained = db.execute(
                    "SELECT state FROM operations WHERE id=?", (original["operation_id"],)
                ).fetchone()
            if retained != ("SUCCEEDED",):
                raise RuntimeError("Upgrade did not retain the completed Agent journal record")
            await control("start")
            resumed_connection = await online(initial_connection["epoch"])
            if (
                resumed_connection["info"]["agent_boot_id"]
                == initial_connection["info"]["agent_boot_id"]
            ):
                raise RuntimeError("Upgraded Agent did not create a new boot identity")
            replay = await execute()
            if (
                replay["operation_id"] != original["operation_id"]
                or (workspace / "counter.txt").read_text() != "1"
            ):
                raise RuntimeError("Upgrade repeated a completed operation")
            report["steps"].append("upgraded Agent resumes with no duplicate side effect")
            import winreg

            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
            ) as key:
                winreg.SetValueEx(
                    key,
                    metadata["app_id"],
                    0,
                    winreg.REG_SZ,
                    '"' + str(executable) + '" racp-background-agent',
                )
            uninstallers = list(installed.glob("Uninstall*.exe"))
            if len(uninstallers) != 1:
                raise RuntimeError("Fixture uninstaller was not found")
            await command([str(uninstallers[0]), "/S", "_?=" + str(installed)])
            if executable.exists() or python.exists():
                raise RuntimeError("Uninstaller left application binaries")
            remaining = await asyncio.to_thread(
                lambda: [
                    item
                    for item in installed.rglob("*")
                    if item.is_file() and item != uninstallers[0]
                ]
            )
            if remaining:
                raise RuntimeError("Uninstaller left bundled assets, including long paths")
            if hashlib.sha256(credentials.read_bytes()).hexdigest() != credential_hash:
                raise RuntimeError("Uninstaller removed the retained credential")
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
            ) as key:
                try:
                    winreg.QueryValueEx(key, metadata["app_id"])
                except FileNotFoundError:
                    pass
                else:
                    raise RuntimeError("Uninstaller left fixture startup registration")
            report["steps"].append("uninstall removes binaries/startup, preserves user state")
            report["status"] = "PASS"
            report["device_id"] = device["device_id"]
            report["credential_sha256"] = credential_hash
            report["backup_count"] = len(backups)
            report["installed_versions"] = [first["version"], second["version"]]
            report["connection_epochs"] = [initial_connection["epoch"], resumed_connection["epoch"]]
            report["operation_id"] = original["operation_id"]
            await asyncio.to_thread(
                Path("dist/installer-acceptance-result.json").write_text,
                json.dumps(report, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps({"status": "PASS", "steps": report["steps"]}))
        finally:
            if python.exists():
                await control("stop")
            server.should_exit = True
            await asyncio.wait_for(task, 15)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--resume", action="store_true", help="Recover only this existing test fixture"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.fixture.absolute(), args.resume)))


if __name__ == "__main__":
    main()
