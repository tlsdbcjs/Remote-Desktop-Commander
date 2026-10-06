import argparse
import json
import sys
from pathlib import Path

from racp_protocol.plugins import PluginManifest
from racp_protocol.reversing import RE_MODELS, RE_READS

from racp_agent.plugins.config import PluginConfig, PluginInstallation
from racp_agent.plugins.ghidra_runtime import (
    GhidraRuntimeCatalog,
    file_hash,
    runtime_files,
    runtime_version,
)
from racp_agent.providers.private import private_directory


def create_ghidra_installation(
    directory: Path, installation: Path, java: Path
) -> PluginInstallation:
    directory = private_directory(directory)
    if any(not path.is_absolute() or str(path).startswith("\\\\") for path in [installation, java]):
        raise ValueError("Ghidra and Java must be explicit absolute local paths")
    installation, java = installation.resolve(strict=True), java.resolve(strict=True)
    runtime = GhidraRuntimeCatalog(
        version=runtime_version(installation),
        installation=installation,
        java=java,
        java_sha256=file_hash(java),
        files={name: file_hash(path) for name, path in runtime_files(installation).items()},
    )
    catalog = directory / "ghidra-runtime.json"
    with catalog.open("x", encoding="utf-8") as stream:
        stream.write(runtime.model_dump_json(indent=2))
    file = directory / "ghidra-manifest.json"
    names = ["re.open", "re.query", "re.command", "re.close", "re.keepalive"]
    manifest = PluginManifest.model_validate(
        {
            "name": "ghidra",
            "version": "1.0.0",
            "backend_name": "ghidra-headless",
            "backend_version": runtime.version,
            "capabilities": ["static-analysis"],
            "health_timeout_ms": 30000,
            "command": [
                sys.executable,
                "-I",
                "-m",
                "racp_agent.plugins.ghidra_plugin",
                "--manifest",
                str(file),
                "--runtime-catalog",
                str(catalog),
                "--runtime-sha256",
                file_hash(catalog),
            ],
            "working_directory": str(directory),
            "required_permissions": ["re.read", "re.mutate"],
            "operations": [
                {
                    "name": name,
                    "capability": "static-analysis",
                    "input_schema": {"type": "object", **RE_MODELS[name].model_json_schema()},
                    "output_schema": {"type": "object"},
                    "permission_scope": "re.read" if name in RE_READS else "re.mutate",
                    "side_effect": name not in RE_READS,
                    "execution_modes": ["sync", "job"],
                    "max_timeout_ms": 86400000,
                }
                for name in names
            ],
        }
    )
    with file.open("x", encoding="utf-8") as stream:
        stream.write(manifest.model_dump_json(indent=2))
    (directory / "ghidra-backend-provenance.json").write_text(
        json.dumps(
            {
                "backend": "Ghidra",
                "version": runtime.version,
                "bundled": False,
                "license": "Apache-2.0",
                "runtime_catalog_sha256": file_hash(catalog),
                "java_executable_sha256": runtime.java_sha256,
                "source": "https://github.com/NationalSecurityAgency/ghidra/",
                "upstream_sbom_sha256": file_hash(installation / "bom.json"),
                "upstream_license_sha256": file_hash(installation / "LICENSE"),
                "licenses_directory": str(installation / "licenses"),
                "adapter_protocol": 1,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return PluginInstallation(
        manifest=file, sha256=file_hash(file), permissions=manifest.required_permissions
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare an explicit local Ghidra plugin approval")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--ghidra", type=Path, required=True)
    parser.add_argument("--java", type=Path, required=True)
    args = parser.parse_args()
    if any(not path.is_absolute() for path in [args.directory, args.ghidra, args.java]):
        parser.error("approval directory, Ghidra directory and Java executable must be absolute")
    config_path = args.directory / "plugin-config.json"
    if config_path.exists():
        parser.error("plugin configuration already exists; choose a fresh directory")
    installed = create_ghidra_installation(args.directory, args.ghidra, args.java)
    with config_path.open("x", encoding="utf-8") as stream:
        stream.write(PluginConfig(plugins=[installed]).model_dump_json(indent=2) + "\n")
    print(config_path)


if __name__ == "__main__":
    main()
