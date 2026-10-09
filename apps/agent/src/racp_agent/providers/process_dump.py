"""Typed process dump collection; target identity and byte budget are compulsory."""

import ctypes
import hashlib
import os
import struct
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
from racp_agent import process_dump
from racp_agent.plugins.process import ContainedCommand
from racp_agent.providers.owned_recipe import OwnedArtifactRecipe
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import Capability
from racp_protocol.provider_models import ProcessDump


class ProcessDumpProvider(OwnedArtifactRecipe):
    suffix = ".dmp"
    media_type = "application/octet-stream"

    def __init__(self, spool: Path) -> None:
        super().__init__(spool)
        self.extra_protected_pids: Callable[[], set[int]] = lambda: set()

    def capability(self) -> Capability:
        supported = os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 8
        return Capability(
            name="process_dump",
            version="1.0.0",
            operations=["process.dump"],
            supported=supported,
            enabled=supported,
            healthy=supported,
            unavailable_reason=None if supported else "requires_windows_x64",
            attributes={
                "backend": "Windows DbgHelp",
                "modes": ["mini", "full"],
                "max_bytes": 256 * 1024**2,
                "bounded_io": True,
                "manual_analysis_tool_required": False,
                "live_debugging": False,
                "containment": "windows-job-object",
            },
        )

    def validate(self, payload: dict[str, Any], context: ExecutionContext) -> ProcessDump:
        data = ProcessDump.model_validate(payload)
        if data.agent_boot_id != context.agent_boot_id:
            raise RACPError("PRECONDITION_FAILED", "Agent boot identity changed", layer="provider")
        protected = {
            os.getpid(),
            *[p.pid for p in psutil.Process().parents()],
            *self.extra_protected_pids(),
            *self.protected_pids(),
        }
        if data.pid in protected:
            raise RACPError(
                "PERMISSION_DENIED", "Protected process cannot be dumped", layer="provider"
            )
        try:
            if abs(psutil.Process(data.pid).create_time() - data.create_time) > 1e-6:
                raise RACPError("PRECONDITION_FAILED", "PID identity changed", layer="provider")
        except psutil.NoSuchProcess:
            raise RACPError(
                "PROCESS_NOT_FOUND", "Target no longer exists", layer="provider"
            ) from None
        except psutil.AccessDenied:
            raise RACPError("PERMISSION_DENIED", "Target access denied", layer="provider") from None
        return data

    def command(self, data: ProcessDump, path: Path) -> ContainedCommand:
        return ContainedCommand(
            [
                sys.executable,
                "-I",
                str(Path(process_dump.__file__).resolve()),
                "--pid",
                str(data.pid),
                "--create-time",
                str(data.create_time),
                "--mode",
                data.mode,
                "--max-bytes",
                str(data.max_bytes),
                "--output",
                str(path),
            ],
            str(self.spool),
        )

    def verify(self, path: Path, receipt: dict[str, Any], data: ProcessDump) -> None:
        with self.guard.parent(path) as parent:
            with os.fdopen(parent.open(path.name, os.O_RDONLY), "rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                if size > data.max_bytes:
                    raise RACPError(
                        "RESOURCE_EXHAUSTED", "Dump exceeded byte budget", layer="provider"
                    )
                header = stream.read(32)
                if len(header) != 32:
                    raise RACPError(
                        "CAPABILITY_UNAVAILABLE", "Incomplete dump header", layer="provider"
                    )
                magic, version, count, directory, _, _, flags = struct.unpack("<IIIIIIQ", header)
                valid = (
                    magic == 0x504D444D
                    and version & 0xFFFF == 0xA793
                    and 1 <= count <= 1024
                    and 32 <= directory <= size - count * 12
                    and bool(flags & 2) == (data.mode == "full")
                )
                if valid:
                    stream.seek(directory)
                    for _ in range(count):
                        _, length, rva = struct.unpack("<III", stream.read(12))
                        if length and (rva < 32 or rva + length > size):
                            valid = False
                            break
                stream.seek(0)
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (
            not valid
            or receipt.get("sha256") != digest
            or receipt.get("bytes") != size
            or receipt.get("target_pid") != data.pid
            or abs(receipt.get("target_create_time", 0) - data.create_time) > 1e-6
            or receipt.get("mode") != data.mode
            or receipt.get("complete") is not True
            or receipt.get("process_handle_closed") is not True
            or receipt.get("bounded_io") is not True
        ):
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "Invalid target dump receipt", layer="provider"
            )
