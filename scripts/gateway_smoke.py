"""Verify the extracted Gateway ZIP using isolated TLS/state and owned child processes."""

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import socket
import subprocess
import time
import zipfile
from pathlib import Path

import httpx
import httpx2
import psutil
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from racp_sdk.connection_file import ConnectionFile
from racp_sdk.security import SecretStore, tls_context


async def catalog(origin: str, state: Path, owner: str, version: str) -> int:
    async with httpx2.AsyncClient(
        verify=tls_context(state / "ca.pem"),
        trust_env=False,
        headers={"Authorization": "Bearer " + owner},
    ) as http:
        async with Client(streamable_http_client(origin + "/mcp/", http_client=http)) as client:
            assert client.server_info is not None and client.server_info.version == version
            tools = await client.list_tools()
            assert any(tool.name == "device_list" for tool in tools.tools)
            result = await client.call_tool("device_list", {})
            assert not result.is_error
            return len(tools.tools)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(args.archive) as archive:
        assert archive.testzip() is None
        for entry in archive.infolist():
            target = (output / entry.filename).resolve()
            assert target.is_relative_to(output)
            assert not {".racp", "owner.bin", "server.key"}.intersection(Path(entry.filename).parts)
        archive.extractall(output)
    bundles = list(output.iterdir())
    assert len(bundles) == 1
    bundle = bundles[0]
    manifest = json.loads((bundle / "gateway-manifest.json").read_text())
    for entry in manifest["files"]:
        file = bundle / entry["file"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry["sha256"]
    assert not (bundle / "runtime/Lib/site-packages/racp_agent").exists()
    state = bundle / ".racp/host"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    powershell = shutil.which("powershell.exe")
    assert powershell is not None
    assert not list(bundle.glob("*.cmd"))
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "RemoteSigned",
        "-File",
        str(bundle / "Start-Gateway.ps1"),
        "-BindAddress",
        "127.0.0.1",
        "-Port",
        str(port),
    ]
    with (output / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=bundle,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        owned = psutil.Process(process.pid)
        identity = owned.create_time()
        try:
            deadline = time.monotonic() + 90
            while True:
                assert process.poll() is None, "Packaged Gateway exited during startup"
                if (state / "ca.pem").exists():
                    try:
                        response = httpx.get(
                            origin + "/healthz",
                            verify=tls_context(state / "ca.pem"),
                            trust_env=False,
                            timeout=1,
                        )
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Packaged Gateway readiness timeout")
                time.sleep(0.2)
            owner = SecretStore(state / "owner.bin").load()

            def helper(filename: str, *extra: str) -> str:
                result = subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "RemoteSigned",
                        "-File",
                        str(bundle / filename),
                        *extra,
                    ],
                    cwd=bundle,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    stdin=subprocess.DEVNULL,
                    timeout=60,
                )
                assert result.returncode == 0, filename + " failed"
                assert owner["token"] not in result.stdout + result.stderr
                return result.stdout

            with httpx.Client(
                base_url=origin,
                verify=tls_context(state / "ca.pem"),
                trust_env=False,
                timeout=10,
            ) as http:
                assert http.get("/readyz").status_code == 401
                page = http.get("/console/")
                assert page.status_code == 200
                assets = re.findall(r'(?:src|href)="(/console/assets/[^\"]+)"', page.text)
                assert len(assets) >= 2
                assert all(http.get(asset).status_code == 200 for asset in assets)
                assert http.get("/mcp/").status_code == 401
                authorization = {"Authorization": "Bearer " + owner["token"]}
                assert http.get("/readyz", headers=authorization).status_code == 200
                helper("Create-Connection-File.ps1", "-NoOpen")
                files = list((state / "connection-files").glob("*.racp"))
                assert len(files) == 1
                connection = ConnectionFile.model_validate_json(
                    files[0].read_text(encoding="utf-8")
                )
                assert connection.gateway == origin
                assert connection.ca_pem == (state / "ca.pem").read_text()
                registered = http.post("/agent/v1/enroll", json={"token": connection.token})
                assert registered.status_code == 200
                assert (
                    http.post("/agent/v1/enroll", json={"token": connection.token}).status_code
                    == 401
                )
                helper("Create-Console-Login.ps1", "-NoOpen")
                login = next((state / "console-login").glob("*.txt")).read_text(encoding="utf-8")
                assert owner["token"] not in login
                code = login.splitlines()[0].split(": ", 1)[1]
                authenticated = http.post(
                    "/api/v1/console/session",
                    json={"setup_secret": code},
                    headers={"Origin": origin},
                )
                assert authenticated.status_code == 200
                assert http.get("/api/v1/devices").status_code == 200
                csrf = http.get("/api/v1/console/session").json()["csrf_token"]
                assert (
                    http.delete(
                        "/api/v1/console/session", headers={"Origin": origin, "X-CSRF-Token": csrf}
                    ).status_code
                    == 200
                )
                assert (
                    http.post(
                        "/api/v1/console/session",
                        json={"setup_secret": code},
                        headers={"Origin": origin},
                    ).status_code
                    == 401
                )
            status_output = helper("Status-Gateway.ps1")
            assert (
                "owner_bearer" in status_output and registered.json()["device_id"] in status_output
            )
            count = asyncio.run(catalog(origin, state, owner["token"], manifest["version"]))
            result = {
                "version": manifest["version"],
                "file_hashes_verified": len(manifest["files"]),
                "zip_crc": "pass",
                "fresh_extracted_powershell_launch": "pass",
                "console_and_assets": "pass",
                "tls_readiness": "pass",
                "connection_file_and_one_use_enrollment": "pass",
                "console_one_use_login": "pass",
                "status_powershell": "pass",
                "loopback_mcp_catalog_tools": count,
                "external_oauth_login": "not_run",
                "agent_execution": "not_run",
            }
            (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
        finally:
            # Cleanup is confined to this fresh PowerShell tree, never a port-based kill.
            if process.poll() is None and owned.create_time() == identity:
                children = owned.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                owned.terminate()
                psutil.wait_procs([owned, *children], timeout=10)


if __name__ == "__main__":
    main()
