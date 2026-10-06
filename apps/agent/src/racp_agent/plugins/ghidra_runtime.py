"""Pin an explicitly installed Ghidra runtime; remote installations are never accepted."""

import hashlib
import os
import re
from pathlib import Path

from pydantic import Field, model_validator
from racp_domain.models import RACPError
from racp_protocol.models import StrictModel

from racp_agent.plugins.manifest import decode_json
from racp_agent.providers.paths import is_link


def runtime_files(root: Path) -> dict[str, Path]:
    if (
        not root.is_absolute()
        or str(root).startswith("\\\\")
        or any(is_link(p.lstat()) for p in [root, *root.parents])
    ):
        raise PermissionError("Ghidra runtime must be an absolute local directory without links")
    files: dict[str, Path] = {}
    for directory, names, leaves in os.walk(root, followlinks=False):
        if Path(directory) == root:
            names[:] = [name for name in names if name != "docs"]
        for name in [*names, *leaves]:
            path = Path(directory) / name
            if is_link(path.lstat()):
                raise PermissionError("Ghidra runtime cannot contain links")
        for leaf in leaves:
            path = Path(directory) / leaf
            files[path.relative_to(root).as_posix()] = path
            if len(files) > 20000:
                raise ValueError("Ghidra runtime inventory exceeds limit")
    return dict(sorted(files.items()))


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_version(root: Path) -> str:
    properties = (root / "Ghidra/application.properties").read_text(encoding="utf-8")
    match = re.search(r"^application.version=([0-9.]+)$", properties, re.MULTILINE)
    if match is None:
        raise ValueError("Ghidra version is unavailable")
    return match[1]


class GhidraRuntimeCatalog(StrictModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    installation: Path
    java: Path
    java_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: dict[str, str] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def local_paths(self) -> "GhidraRuntimeCatalog":
        if any(
            not path.is_absolute() or str(path).startswith("\\\\")
            for path in [self.installation, self.java]
        ):
            raise ValueError("Ghidra/Java paths must be absolute")
        if any(
            Path(key).is_absolute()
            or ".." in Path(key).parts
            or "\\" in key
            or ":" in key
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            for key, digest in self.files.items()
        ):
            raise ValueError("invalid runtime file hash/path")
        return self


class GhidraRuntime:
    def __init__(self, path: Path, approved_sha256: str) -> None:
        with path.open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if hashlib.sha256(raw).hexdigest() != approved_sha256:
            raise ValueError("Ghidra runtime catalog approval differs")
        decode_json(raw, 4 * 1024 * 1024)
        self.catalog = GhidraRuntimeCatalog.model_validate_json(raw)
        self.signatures: dict[str, tuple[int, int, int]] = {}

    def verify(self, *, force: bool = False) -> None:
        catalog = self.catalog
        files = runtime_files(catalog.installation)
        if files.keys() != catalog.files.keys():
            raise RACPError(
                "PLUGIN_VERSION_MISMATCH", "Ghidra runtime file inventory changed", layer="plugin"
            )
        files["@java"] = catalog.java
        for key, path in files.items():
            if any(is_link(p.lstat()) for p in [path, *path.parents]):
                raise PermissionError("approved runtime cannot follow links")
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if force or self.signatures.get(key) != signature:
                expected = catalog.java_sha256 if key == "@java" else catalog.files[key]
                if file_hash(path) != expected:
                    raise RACPError(
                        "PLUGIN_VERSION_MISMATCH",
                        "approved Ghidra/Java file changed",
                        layer="plugin",
                    )
                self.signatures[key] = signature
        if runtime_version(catalog.installation) != catalog.version:
            raise ValueError("Ghidra runtime version differs")
