import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from racp_agent.plugins import ghidra_plugin
from racp_agent.plugins.ghidra_installation import create_ghidra_installation
from racp_agent.plugins.ghidra_runtime import (
    GhidraRuntime,
    GhidraRuntimeCatalog,
    file_hash,
    runtime_files,
)
from racp_domain.models import RACPError


def runtime(tmp_path: Path) -> tuple[GhidraRuntime, Path]:
    root = tmp_path / "runtime"
    (root / "Ghidra").mkdir(parents=True)
    (root / "Ghidra/application.properties").write_text("application.version=12.1.4\n")
    module = root / "Ghidra/module.jar"
    module.write_bytes(b"approved module")
    java = tmp_path / "java.exe"
    java.write_bytes(b"approved java")
    catalog = GhidraRuntimeCatalog(
        version="12.1.4",
        installation=root,
        java=java,
        java_sha256=file_hash(java),
        files={name: file_hash(file) for name, file in runtime_files(root).items()},
    )
    path = tmp_path / "runtime.json"
    path.write_text(catalog.model_dump_json())
    return GhidraRuntime(path, file_hash(path)), module


def test_execution_rehash_detects_content_change_even_with_restored_metadata(
    tmp_path: Path,
) -> None:
    installed, module = runtime(tmp_path)
    installed.verify()
    stat = module.stat()
    module.write_bytes(b"replaced module")
    os.utime(module, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(RACPError) as error:
        installed.verify(force=True)
    assert error.value.error.code == "PLUGIN_VERSION_MISMATCH"


def test_runtime_addition_and_catalog_replacement_require_new_approval(tmp_path: Path) -> None:
    installed, module = runtime(tmp_path)
    installed.verify()
    (module.parent / "additional.jar").write_bytes(b"new executable classes")
    with pytest.raises(RACPError):
        installed.verify()
    path = tmp_path / "runtime.json"
    original = file_hash(path)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="approval differs"):
        GhidraRuntime(path, original)


@pytest.mark.parametrize("name", ["../module.jar", "C:/module.jar", "a\\module.jar"])
def test_runtime_catalog_cannot_reference_external_files(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValidationError):
        GhidraRuntimeCatalog(
            version="12.1.4",
            installation=tmp_path,
            java=tmp_path / "java.exe",
            java_sha256="0" * 64,
            files={name: hashlib.sha256(b"x").hexdigest()},
        )


async def test_unsupported_containment_never_starts_java(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, module = runtime(tmp_path)
    root = module.parent.parent
    (root / "LICENSE").write_text("fixture license")
    (root / "bom.json").write_text("{}")
    installation = create_ghidra_installation(tmp_path / "approval", root, tmp_path / "java.exe")
    manifest = json.loads(installation.manifest.read_text())
    plugin = ghidra_plugin.GhidraPlugin(
        installation.manifest,
        installation.manifest.parent / "ghidra-runtime.json",
        manifest["command"][-1],
    )
    monkeypatch.setattr(ghidra_plugin, "os", SimpleNamespace(name="posix"))
    with pytest.raises(RACPError) as error:
        await plugin.health()
    assert error.value.error.code == "CAPABILITY_UNAVAILABLE"
    assert plugin.driver.current is None
