"""Stage a native, self-contained Agent runtime for the Electron client."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import playwright
from packaging.requirements import Requirement
from racp_domain.version import VERSION


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--browser-cache", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 12, 11):
        raise SystemExit("Use the pinned Python 3.12.11 runtime")
    systems = {"Windows": "win32", "Darwin": "darwin", "Linux": "linux"}
    architectures = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
    target = systems[platform.system()]
    arch = architectures[platform.machine().lower()]
    directory_target = {"win32": "win", "darwin": "mac", "linux": "linux"}[target]
    output = (
        args.output or Path("dist/client-agent") / VERSION / f"{directory_target}-{arch}"
    ).absolute()
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    shutil.copytree(
        Path(sys.base_prefix),
        runtime,
        symlinks=True,
        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc"),
    )
    installed = Path(sysconfig.get_path("purelib")).resolve()
    site = runtime / Path(sysconfig.get_path("purelib")).relative_to(Path(sys.prefix))
    site.mkdir(parents=True)
    exported = subprocess.check_output(
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
    ).decode("utf-8")
    for line in exported.splitlines():
        line = line.strip()
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        distribution = importlib.metadata.distribution(requirement.name)
        if not requirement.specifier.contains(distribution.version) or distribution.files is None:
            raise RuntimeError(
                "Installed dependency differs from frozen export: " + requirement.name
            )
        for entry in distribution.files:
            origin = Path(distribution.locate_file(entry)).resolve()
            if (
                not origin.is_relative_to(installed)
                or origin.suffix == ".pyc"
                or "__pycache__" in origin.parts
            ):
                continue
            destination = site / origin.relative_to(installed)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
    for name in ["agent", "domain", "protocol", "policy", "observability", "sdk"]:
        with zipfile.ZipFile(Path("dist") / f"racp_{name}-{VERSION}-py3-none-any.whl") as archive:
            for item in archive.infolist():
                destination = (site / item.filename).resolve()
                if not destination.is_relative_to(site.resolve()):
                    raise ValueError("Wheel path escapes the client package")
                if not item.is_dir():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(item))
    executable = runtime / ("python.exe" if target == "win32" else "bin/python3")
    subprocess.run(
        [
            str(executable),
            "-I",
            "-B",
            "-c",
            "import racp_agent.desktop_control,psutil,playwright; print('native Agent imports OK')",
        ],
        check=True,
        cwd=output,
    )
    registry = json.loads(
        (Path(playwright.__file__).parent / "driver/package/browsers.json").read_text()
    )
    for browser in registry["browsers"]:
        if browser["name"] not in {"chromium", "chromium-headless-shell", "ffmpeg", "winldd"}:
            continue
        name = browser["name"].replace("-", "_") + "-" + browser["revision"]
        source = args.browser_cache / name
        if not source.is_dir():
            if browser["name"] == "winldd" and target != "win32":
                continue
            raise ValueError("Missing pinned browser runtime: " + name)
        shutil.copytree(source, output / "browsers" / name, symlinks=True)
    files = [
        {
            "file": p.relative_to(output).as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(output.rglob("*"))
        if p.is_file() and p.suffix != ".pyc" and "__pycache__" not in p.parts
    ]
    (output / "agent-manifest.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "platform": target,
                "arch": arch,
                "python": "3.12.11",
                "signed": False,
                "lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
                "files": files,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "platform": target, "arch": arch}))


if __name__ == "__main__":
    main()
