"""Build a native Windows x64 Gateway ZIP, including Python and the Web Console."""

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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from packaging.requirements import Requirement
from pydantic import BaseModel, ConfigDict
from racp_domain.version import VERSION

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PackagingConfig:
    node: Path
    platform: Literal["win"] = "win"
    arch: Literal["x64"] = "x64"
    targets: tuple[Literal["setup", "portable"], ...] = ("portable",)
    dry_run: bool = False
    output_dir: Path | None = None


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    size_bytes: int
    sha256: str
    target: Literal["setup", "portable"]


class BuildManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = VERSION
    signed: bool = False
    publish: bool = False
    platform: Literal["win-x64"] = "win-x64"
    targets: list[Literal["setup", "portable"]]
    artifacts: list[ArtifactManifest]


def sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def package_gateway(config: PackagingConfig) -> BuildManifest | dict[str, object]:
    """Run the supported Gateway packaging CLI through a typed Python interface."""
    targets = list(dict.fromkeys(config.targets))
    if not targets:
        raise ValueError("at least one Gateway packaging target is required")
    command = [
        sys.executable,
        str(ROOT / "scripts/build_gateway.py"),
        "--platform",
        config.platform,
        "--arch",
        config.arch,
        "--targets",
        ",".join(targets),
        "--node",
        str(config.node),
    ]
    if config.output_dir is not None:
        command.extend(["--output-dir", str(config.output_dir)])
    if config.dry_run:
        command.append("--dry-run")
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(result.stdout)
    subprocess.run(command, cwd=ROOT, check=True)
    output = (
        config.output_dir.resolve()
        if config.output_dir is not None
        else ROOT / "dist/gateway" / VERSION / "win-x64"
    )
    manifest_path = output / "build-manifest.json"
    return BuildManifest.model_validate_json(manifest_path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--platform", choices=["win"], default="win")
    parser.add_argument("--arch", choices=["x64"], default="x64")
    parser.add_argument("--targets", default="portable")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    deployment = json.loads(
        (ROOT / "apps/gateway/build/deployment.json").read_text(encoding="utf-8")
    )
    targets = [value.strip() for value in args.targets.split(",") if value.strip()]
    if not targets or any(value not in deployment["targets"] for value in targets):
        raise SystemExit("Gateway targets must be a comma-separated subset of setup,portable")
    makensis = shutil.which("makensis.exe") or shutil.which("makensis")
    compiler = deployment["installer"]["compiler"]
    output = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else ROOT / "dist/gateway" / VERSION / "win-x64"
    )
    name = f"RACP-Gateway-{VERSION}-win-x64"
    bundle = output / name
    if args.dry_run:
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "output": str(output),
                    "platform": f"{args.platform}-{args.arch}",
                    "targets": targets,
                    "deployment_schema": deployment["schema_version"],
                    "setup_engine": "nsis",
                    "setup_engine_available": makensis is not None,
                    "publish": False,
                }
            )
        )
        return
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Build Gateway on native Windows x64")
    if sys.version_info[:3] != (3, 12, 11):
        raise SystemExit("Use pinned CPython 3.12.11")
    if "setup" in targets and makensis is None:
        raise SystemExit("Setup target requires makensis.exe (NSIS) on PATH")
    if "setup" in targets:
        assert makensis is not None
        makensis_path = Path(makensis).resolve()
        reported = subprocess.check_output(
            [str(makensis_path), "/VERSION"], text=True, encoding="utf-8"
        ).strip()
        if reported != compiler["reported_version"]:
            expected = compiler["reported_version"]
            raise SystemExit(
                f"NSIS compiler version mismatch: expected {expected}, got {reported}"
            )
        if sha256(makensis_path) != compiler["makensis_sha256"]:
            raise SystemExit("NSIS compiler executable SHA-256 differs from deployment pin")
        archive_path = next(
            (
                parent / compiler["archive"]
                for parent in (makensis_path.parent, *makensis_path.parents)
                if (parent / compiler["archive"]).is_file()
            ),
            None,
        )
        if archive_path is None:
            raise SystemExit("Pinned NSIS compiler archive was not found beside the compiler cache")
        if sha256(archive_path) != compiler["archive_sha256"]:
            raise SystemExit("NSIS compiler archive SHA-256 differs from deployment pin")
    if output.exists():
        raise SystemExit("Preserve existing artifacts; quarantine only a failed batch before retry")
    node = args.node.resolve(strict=True)

    def run(command: list[str]) -> None:
        print("Running: " + subprocess.list2cmdline(command), flush=True)
        subprocess.run(command, cwd=ROOT, check=True)

    # Installed dependencies must match the frozen graph before any runtime is copied.
    run([sys.executable, "scripts/console_build.py", "--node", str(node)])
    wheels = output / "wheels"
    run(["uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheels)])
    bundle.mkdir()
    runtime = bundle / "runtime"
    shutil.copytree(
        Path(sys.base_prefix),
        runtime,
        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc"),
    )
    installed = Path(sysconfig.get_path("purelib")).resolve()
    site = runtime / "Lib/site-packages"
    site.mkdir(parents=True)
    exported = subprocess.check_output(
        [
            "uv",
            "export",
            "--package",
            "racp-gateway",
            "--frozen",
            "--no-dev",
            "--no-emit-workspace",
            "--no-hashes",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT,
        text=True,
    )
    dependencies = []
    for line in exported.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        distribution = importlib.metadata.distribution(requirement.name)
        if not requirement.specifier.contains(distribution.version) or distribution.files is None:
            raise RuntimeError("Frozen dependency mismatch: " + requirement.name)
        dependencies.append({"name": requirement.name, "version": distribution.version})
        for entry in distribution.files:
            origin = Path(distribution.locate_file(entry)).resolve()
            if not origin.is_relative_to(installed) or origin.suffix == ".pyc":
                continue
            if "__pycache__" in origin.parts:
                continue
            destination = site / origin.relative_to(installed)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
    for package in ["gateway", "domain", "protocol", "policy", "observability", "sdk"]:
        with zipfile.ZipFile(wheels / f"racp_{package}-{VERSION}-py3-none-any.whl") as archive:
            for entry in archive.infolist():
                destination = (site / entry.filename).resolve()
                if not destination.is_relative_to(site.resolve()):
                    raise ValueError("Wheel path escapes the runtime")
                if not entry.is_dir():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(entry))
    shutil.copytree(ROOT / "apps/console/dist", site / "racp_gateway/static/console")
    scripts = bundle / "scripts"
    scripts.mkdir()
    for script in [
        "gateway_host.py",
        "create_connection_file.py",
        "create_console_login.py",
        "issue_desktop_lab_ticket.py",
    ]:
        shutil.copy2(ROOT / "scripts" / script, scripts / script)
    for launcher in sorted((ROOT / "scripts/host").glob("*.ps1")):
        shutil.copy2(launcher, bundle / launcher.name)
    guide = (ROOT / "docs/guides/gateway-deployment-guide.md").read_text(encoding="utf-8")
    readme = guide.split("## 5. 관련 문서")[0]
    readme = readme.replace("- [5. 관련 문서](#5-관련-문서)\n", "")
    (bundle / "README.md").write_text(readme, encoding="utf-8")
    run(
        [
            str(runtime / "python.exe"),
            "-I",
            "-B",
            "-c",
            "import racp_gateway.main,cryptography,win32crypt,win32security,psutil; "
            "from racp_domain.version import VERSION; "
            f"assert VERSION == {VERSION!r}; print('native Gateway imports/version OK')",
        ]
    )
    run(
        [
            str(runtime / "python.exe"),
            "-I",
            "-B",
            "-c",
            (
                "import win32serviceutil; "
                "print(win32serviceutil.LocatePythonServiceExe())"
            ),
        ]
    )
    service_host = runtime / "pythonservice.exe"
    if not service_host.is_file():
        raise RuntimeError("pywin32 service host was not staged beside the bundled Python runtime")
    if not any(runtime.glob("pywintypes*.dll")):
        raise RuntimeError("pywin32 runtime DLL was not staged beside the bundled Python runtime")
    manifest = {
        "version": VERSION,
        "kind": "development-gateway",
        "platform": "win-x64",
        "python": "3.12.11",
        "signed": False,
        "lock_sha256": sha256(ROOT / "uv.lock"),
        "console_lock_sha256": sha256(ROOT / "pnpm-lock.yaml"),
        "dependencies": dependencies,
        "files": [
            {"file": p.relative_to(bundle).as_posix(), "sha256": sha256(p)}
            for p in sorted(bundle.rglob("*"))
            if p.is_file()
        ],
    }
    (bundle / "gateway-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    archive_path = output / (name + ".zip")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(output))
    artifacts = [{
        "file": archive_path.name,
        "size_bytes": archive_path.stat().st_size,
        "sha256": sha256(archive_path),
        "target": "portable",
    }]
    if "setup" in targets:
        assert makensis is not None
        setup_path = output / (name + "-setup.exe")
        run(
            [
                makensis,
                "/WX",
                f"/DSOURCE_ROOT={bundle}",
                f"/DOUTPUT_FILE={setup_path}",
                f"/DVERSION={VERSION}",
                str(ROOT / "apps/gateway/build/installer.nsi"),
            ]
        )
        if not setup_path.is_file():
            raise RuntimeError("NSIS did not produce the Gateway setup executable")
        artifacts.append(
            {
                "file": setup_path.name,
                "size_bytes": setup_path.stat().st_size,
                "sha256": sha256(setup_path),
                "target": "setup",
            }
        )
    (output / "build-manifest.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "signed": False,
                "publish": False,
                "platform": "win-x64",
                "targets": targets,
                "artifacts": artifacts,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "SHA256SUMS.txt").write_text(
        "".join(item["sha256"] + "  " + item["file"] + "\n" for item in artifacts),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "artifacts": artifacts}, indent=2))


if __name__ == "__main__":
    main()
