"""Build an isolated portable Windows Agent from pinned runtime and frozen wheels."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

from racp_sdk.security import require_secure_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument("--browser-cache", type=Path)
    args = parser.parse_args()
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Build on the Windows x64 reference host")
    if sys.version_info[:3] != (3, 12, 11):
        raise SystemExit("Use the pinned Python 3.12.11 runtime")
    require_secure_url(args.gateway)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(getattr(sys, "_base_executable", sys.executable)).parent
    runtime = output / "runtime"
    shutil.copytree(
        source, runtime, ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc")
    )
    requirements = output / "requirements.txt"
    requirements.write_bytes(
        subprocess.check_output(
            [
                "uv",
                "export",
                "--package",
                "racp-agent",
                "--frozen",
                "--no-dev",
                "--no-emit-workspace",
                "--no-hashes",
                "--format",
                "requirements-txt",
            ]
        )
    )
    site = runtime / "Lib/site-packages"
    installed_site = Path(sysconfig.get_path("purelib")).resolve()
    site.mkdir(parents=True)
    for line in requirements.read_text(encoding="utf-8").splitlines():
        value = line.partition(";")[0].strip()
        if not value or value.startswith("#"):
            continue
        name, version = value.split("==", 1)
        distribution = importlib.metadata.distribution(name)
        if distribution.version != version or distribution.files is None:
            raise RuntimeError("Installed dependency differs from frozen export: " + name)
        for entry in distribution.files:
            origin = Path(distribution.locate_file(entry)).resolve()
            if not origin.is_relative_to(installed_site):
                continue  # Generated console entry points are not required by -m workers.
            destination = site / origin.relative_to(installed_site)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
    wheels = [
        Path("dist") / f"racp_{name}-0.1.0-py3-none-any.whl"
        for name in ["agent", "domain", "protocol", "policy", "observability", "sdk"]
    ]
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for item in archive.infolist():
                destination = (site / item.filename).resolve()
                if not destination.is_relative_to(site.resolve()):
                    raise ValueError("Wheel path escapes the client package")
                if not item.is_dir():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(item))
    subprocess.run(
        [
            str(runtime / "python.exe"),
            "-I",
            "-c",
            "import racp_agent,win32crypt,win32job,psutil,pydantic,playwright,jsonschema; "
            "print('portable imports OK')",
        ],
        cwd=output,
        check=True,
    )
    shutil.copy2(args.ca_file, output / "ca.pem")
    if args.browser_cache:
        shutil.copytree(
            args.browser_cache, output / "browsers", ignore=shutil.ignore_patterns(".links")
        )
    shutil.copy2(Path("scripts/portable_client.py"), output / "client.py")
    (output / "client-config.json").write_text(
        json.dumps(
            {
                "gateway": args.gateway.rstrip("/"),
                "profile": "trusted_personal",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for filename, action in [
        ("Start-Client.cmd", "start"),
        ("Status-Client.cmd", "status"),
        ("Stop-Client.cmd", "stop"),
    ]:
        (output / filename).write_text(
            '@echo off\r\ncd /d "%~dp0"\r\n"runtime\\python.exe" -I client.py '
            + action
            + " %*\r\npause\r\n",
            encoding="ascii",
        )
    (output / "README.txt").write_text(
        "RACP Windows x64 portable TEST client (unsigned development build)\n"
        "Extract the ZIP to a local folder. Run Start-Client.cmd, then Status-Client.cmd.\n"
        "Python, packages and Chromium are bundled; no Python/uv installation is required.\n"
        "The Agent connects outbound over HTTPS/WSS to the Gateway in client-config.json.\n"
        "The test profile permits files/commands with your current Windows account.\n"
        "File tools use the bundle workspace folder. Shell commands use OS account permissions.\n"
        "Stop-Client.cmd cancels managed work and stops this Agent.\n"
        "Enrollment is one-use. If no ticket is present, paste the Console enrollment token.\n"
        "Local state: state; files: workspace; logs: state/background/agent.log.\n",
        encoding="utf-8",
    )
    artifacts = [
        {
            "file": str(p.relative_to(output)).replace("\\", "/"),
            "size_bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(output.rglob("*"))
        if p.is_file()
    ]
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "kind": "unsigned-portable-test-agent",
                "python": "3.12.11",
                "signed": False,
                "lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
                "artifacts": artifacts,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"client": str(output), "files": len(artifacts)}))


if __name__ == "__main__":
    main()
