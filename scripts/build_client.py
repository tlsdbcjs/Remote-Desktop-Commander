"""Build native desktop setup and portable artifacts; default to Windows x64."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from racp_domain.version import VERSION

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = {"win": "win32", "mac": "darwin", "linux": "linux"}
ARCHITECTURES = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PLATFORMS, default="win")
    parser.add_argument("--arch", choices=["x64", "arm64"], default="x64")
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "node"))
    parser.add_argument("--browser-cache", type=Path, default=Path(".tools/client-browsers"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    suffix = Path(VERSION) / f"{args.platform}-{args.arch}"
    runtime = ROOT / "dist/client-agent" / suffix
    output = ROOT / "dist/client-desktop" / suffix
    browser_cache = (ROOT / args.browser_cache).resolve()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "platform": args.platform,
                    "arch": args.arch,
                    "runtime": str(runtime),
                    "output": str(output),
                    "targets": json.loads(
                        (ROOT / "apps/client/electron-builder.json").read_text(encoding="utf-8")
                    )[args.platform]["target"],
                    "native_host_required": True,
                    "build_executed": False,
                },
                indent=2,
            )
        )
        return
    if (
        sys.platform != PLATFORMS[args.platform]
        or ARCHITECTURES.get(platform.machine().lower()) != args.arch
    ):
        raise SystemExit("Build on the matching native OS and architecture; use --dry-run to plan")
    if sys.version_info[:3] != (3, 12, 11):
        raise SystemExit("Run with uv run python (pinned Python 3.12.11)")
    if runtime.exists() or output.exists():
        raise SystemExit("Build paths already exist; preserve artifacts and prepare a new version")
    node = args.node.resolve(strict=True)
    environment = dict(os.environ, CSC_IDENTITY_AUTO_DISCOVERY="false")
    environment["PATH"] = str(node.parent) + os.pathsep + environment["PATH"]
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    if subprocess.check_output([str(node), "--version"], text=True).strip() != "v22.23.0":
        raise SystemExit("Use the pinned Node 22.23.0 runtime via --node")
    corepack = node.parent / "node_modules/corepack/dist/corepack.js"
    pnpm = (
        [str(node), str(corepack), "pnpm"]
        if corepack.is_file()
        else [shutil.which("pnpm", path=environment["PATH"]) or "pnpm"]
    )
    if (
        subprocess.check_output([*pnpm, "--version"], env=environment, text=True).strip()
        != "11.19.0"
    ):
        raise SystemExit("Use the pinned pnpm 11.19.0 package manager")

    def run(command: list[str]) -> None:
        print("Running: " + subprocess.list2cmdline(command), flush=True)
        subprocess.run(command, cwd=ROOT, env=environment, check=True)

    run(["uv", "sync", "--all-packages", "--frozen"])
    run([*pnpm, "install", "--frozen-lockfile"])
    run([str(node), "apps/client/node_modules/electron/install.js"])
    run([sys.executable, "scripts/build.py"])
    browser_command = [sys.executable, "-m", "playwright", "install", "chromium"]
    if args.platform == "linux":
        browser_command.append("--with-deps")
    run(browser_command)
    run([sys.executable, "scripts/stage_client_agent.py", "--browser-cache", str(browser_cache)])
    run([*pnpm, "--dir", "apps/client", "build"])
    run([*pnpm, "--dir", "apps/client", "test"])
    run(
        [
            *pnpm,
            "--dir",
            "apps/client",
            "package",
            f"--{args.platform}",
            f"--{args.arch}",
            "--publish",
            "never",
        ]
    )
    expected = {
        "win": [
            f"RACP-Client-{VERSION}-win-{args.arch}-setup.exe",
            f"RACP-Client-{VERSION}-win-{args.arch}-portable.exe",
            f"RACP-Client-{VERSION}-win-{args.arch}.zip",
        ],
        "mac": [
            f"RACP-Client-{VERSION}-mac-{args.arch}.dmg",
            f"RACP-Client-{VERSION}-mac-{args.arch}.zip",
        ],
        "linux": [
            f"RACP-Client-{VERSION}-linux-{args.arch}.AppImage",
            f"RACP-Client-{VERSION}-linux-{args.arch}.deb",
        ],
    }[args.platform]
    artifacts = []
    for name in expected:
        file = output / name
        if not file.is_file():
            raise RuntimeError("Missing desktop artifact: " + name)
        checksum = hashlib.sha256()
        with file.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                checksum.update(chunk)
        artifacts.append(
            {"file": name, "size_bytes": file.stat().st_size, "sha256": checksum.hexdigest()}
        )
    if args.platform == "win":
        run(
            [
                str(node),
                "apps/client/tests/packaged-smoke.cjs",
                str(output / "win-unpacked/RACP Client.exe"),
            ]
        )
        run(
            [
                str(node),
                "apps/client/tests/portable-smoke.cjs",
                str(output / f"RACP-Client-{VERSION}-win-{args.arch}-portable.exe"),
            ]
        )
    manifest = {
        "version": VERSION,
        "platform": args.platform,
        "arch": args.arch,
        "signed": False,
        "kind": "development-desktop",
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "artifacts": artifacts,
    }
    (output / "build-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), "artifacts": artifacts}, indent=2))


if __name__ == "__main__":
    main()
