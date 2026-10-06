import argparse
import hashlib
import json
import sys
from pathlib import Path

from racp_protocol.plugins import PluginManifest
from racp_protocol.reversing import RE_MODELS, RE_READS

from racp_agent.plugins.config import PluginConfig, PluginInstallation
from racp_agent.providers.private import private_directory


def create_gdb_installation(
    directory: Path, executable: Path, backend_version: str
) -> PluginInstallation:
    """Explicit local provisioning; no downloads, PATH lookup or remote configuration."""
    directory = private_directory(directory)
    executable = executable.resolve(strict=True)
    with executable.open("rb") as stream:
        backend_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest_file = directory / "gdb-manifest.json"
    names = [
        "debugger.launch",
        "debugger.command",
        "debugger.close",
        "debugger.info",
        "debugger.registers",
        "debugger.read_memory",
        "debugger.backtrace",
        "debugger.wait",
        "debugger.keepalive",
    ]
    manifest = PluginManifest.model_validate(
        {
            "name": "gdb",
            "version": "1.0.0",
            "backend_name": "gdb-mi",
            "backend_version": backend_version,
            "capabilities": ["debugger"],
            "command": [
                sys.executable,
                "-I",
                "-m",
                "racp_agent.plugins.gdb_plugin",
                "--manifest",
                str(manifest_file),
                "--gdb",
                str(executable),
                "--gdb-sha256",
                backend_sha,
            ],
            "working_directory": str(directory),
            "required_permissions": ["debugger.read", "debugger.mutate"],
            "operations": [
                {
                    "name": name,
                    "capability": "debugger",
                    "input_schema": {"type": "object", **RE_MODELS[name].model_json_schema()},
                    "output_schema": {"type": "object"},
                    "permission_scope": "debugger.read" if name in RE_READS else "debugger.mutate",
                    "side_effect": name not in RE_READS,
                    "execution_modes": ["sync", "job"],
                    "max_timeout_ms": 86400000,
                }
                for name in names
            ],
        }
    )
    with manifest_file.open("x", encoding="utf-8") as stream:
        stream.write(manifest.model_dump_json(indent=2))
    (directory / "gdb-backend-provenance.json").write_text(
        json.dumps(
            {
                "backend": "GNU GDB",
                "version": backend_version,
                "executable": str(executable),
                "sha256": backend_sha,
                "license": "GPL-3.0-or-later",
                "bundled": False,
                "source": "https://sourceware.org/gdb/",
                "adapter_protocol": 1,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return PluginInstallation(
        manifest=manifest_file,
        sha256=hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
        permissions=manifest.required_permissions,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare an explicit local GDB plugin approval")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--gdb", type=Path, required=True)
    parser.add_argument("--backend-version", required=True)
    args = parser.parse_args()
    if not args.directory.is_absolute() or not args.gdb.is_absolute():
        parser.error("directory and GDB executable must be absolute local paths")
    config_path = args.directory / "plugin-config.json"
    if config_path.exists():
        parser.error("plugin configuration already exists; choose a fresh directory")
    installation = create_gdb_installation(args.directory, args.gdb, args.backend_version)
    with config_path.open("x", encoding="utf-8") as stream:
        stream.write(PluginConfig(plugins=[installation]).model_dump_json(indent=2) + "\n")
    print(config_path)


if __name__ == "__main__":
    main()
