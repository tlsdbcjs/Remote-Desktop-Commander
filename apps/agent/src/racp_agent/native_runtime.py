"""Hash-pinned CDB runtime for the managed bridge, without public argv inputs.

The expected manifest digest is supplied by trusted Agent configuration, never
read from an RPC or accepted from the manifest itself. This module does not
install a debugger, authorize native commands or grant redistribution rights.
"""

import hashlib
import os
import re
import struct
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal

from pydantic import Field, field_validator, model_validator
from racp_domain.models import RACPError
from racp_protocol.models import StrictModel

from racp_agent.providers.paths import PathGuard

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Component = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+\.(?:dll|exe)$")]


class NativeRuntimeManifest(StrictModel):
    version: Literal[1] = 1
    engine: Literal["cdb-win-x64"]
    engine_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+\.\d+$", max_length=64)]
    files: dict[Component, Digest] = Field(min_length=3, max_length=16)
    extension: Literal["exts.dll"] | None = None

    @field_validator("version", mode="before")
    @classmethod
    def version_type(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("native runtime schema version must be integer 1")
        return value

    @model_validator(mode="after")
    def complete(self) -> "NativeRuntimeManifest":
        names = [name.casefold() for name in self.files]
        if len(names) != len(set(names)) or not {"cdb.exe", "dbgeng.dll", "dbghelp.dll"} <= set(
            names
        ):
            raise ValueError("native runtime components missing or aliased")
        if any(name.endswith(".exe") and name != "cdb.exe" for name in names):
            raise ValueError("only the fixed CDB executable is allowed")
        if self.extension and self.extension not in names:
            raise ValueError("default extension must be a pinned component")
        return self


@dataclass(frozen=True)
class PinnedNativeRuntime:
    executable: Path
    version: str
    extension: Path | None = None

    def reverse_command(self, pid: int, port: int, working_directory: Path) -> list[str]:
        if type(pid) is not int or not 1 <= pid <= 2**32 - 1:
            raise ValueError("invalid native target PID")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid native loopback port")
        if not working_directory.is_absolute():
            raise ValueError("native working directory must be Agent-owned absolute path")
        command = [
            str(self.executable),
            "-server",
            f"tcp:port={port},clicon=127.0.0.1",
        ]
        if self.extension is not None:
            command.append("-a" + str(self.extension.with_suffix("")))
        command.extend(
            [
                "-p",
                str(pid),
                "-pd",
                "-noshell",
                "-noinh",
                "-y",
                str(working_directory),
            ]
        )
        return command


class ManagedNativeRuntime:
    def __init__(self, root: Path, expected_manifest_sha256: str) -> None:
        if not root.is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
            raise ValueError("native runtime requires a trusted absolute root and digest")
        self.root = root
        self.expected = expected_manifest_sha256

    @contextmanager
    def pin(self) -> Iterator[PinnedNativeRuntime]:
        if os.name != "nt":
            raise RACPError("CAPABILITY_UNAVAILABLE", "Native CDB runtime requires Windows x64")
        import msvcrt

        import win32file

        def open_component(name: str, stack: ExitStack) -> BinaryIO:
            handle = win32file.CreateFile(
                str(self.root / name),
                0x80000000,
                1,
                None,
                3,
                0x00200000 | 0x02000000,
                None,
            )
            try:
                info = win32file.GetFileInformationByHandle(handle)
                if info[0] & (0x400 | 0x10):
                    raise RACPError("PATH_ACCESS_DENIED", "Native runtime link or directory denied")
                fd = msvcrt.open_osfhandle(handle.Detach(), os.O_RDONLY)
            finally:
                handle.Close()
            return stack.enter_context(os.fdopen(fd, "rb"))

        try:
            guard = PathGuard(self.root)
            with ExitStack() as stack:
                stack.enter_context(guard.directory(guard.root))
                manifest_file = open_component("manifest.json", stack)
                raw = manifest_file.read(32769)
                if len(raw) > 32768 or hashlib.sha256(raw).hexdigest() != self.expected:
                    raise RACPError("PRECONDITION_FAILED", "Native runtime manifest digest differs")
                manifest = NativeRuntimeManifest.model_validate_json(raw)
                actual = {item.name.casefold() for item in self.root.iterdir()}
                expected = {name.casefold() for name in manifest.files} | {"manifest.json"}
                if actual != expected:
                    raise RACPError("PRECONDITION_FAILED", "Unmanifested native runtime component")
                total = 0
                for name, digest in manifest.files.items():
                    stream = open_component(name, stack)
                    size = os.fstat(stream.fileno()).st_size
                    total += size
                    if not 64 <= size <= 64 * 1024**2 or total > 128 * 1024**2:
                        raise RACPError("RESOURCE_EXHAUSTED", "Native runtime byte limit")
                    header = stream.read(64)
                    offset = struct.unpack_from("<I", header, 0x3C)[0]
                    if header[:2] != b"MZ" or not 64 <= offset <= size - 6:
                        raise RACPError("CAPABILITY_UNAVAILABLE", "Native runtime is not valid PE")
                    stream.seek(offset)
                    pe = stream.read(6)
                    if pe[:4] != b"PE\0\0" or pe[4:] != b"\x64\x86":
                        raise RACPError(
                            "CAPABILITY_UNAVAILABLE", "Native runtime is not Windows x64"
                        )
                    stream.seek(0)
                    hashed = hashlib.sha256()
                    while chunk := stream.read(65536):
                        hashed.update(chunk)
                    if hashed.hexdigest() != digest:
                        raise RACPError("PRECONDITION_FAILED", "Native component digest differs")
                executable = next(name for name in manifest.files if name.casefold() == "cdb.exe")
                extension = None
                if manifest.extension:
                    selected = next(
                        name for name in manifest.files if name.casefold() == manifest.extension
                    )
                    extension = self.root / selected
                yield PinnedNativeRuntime(
                    self.root / executable, manifest.engine_version, extension
                )
        except (OSError, ValueError) as error:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "Native runtime could not be pinned safely"
            ) from error
