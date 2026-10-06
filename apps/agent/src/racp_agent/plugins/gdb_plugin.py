"""Allowlisted GDB/MI adapter; stdout is the RACP plugin protocol only."""

import argparse
import asyncio
import hashlib
import re
import struct
import sys
import time
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import new_id
from racp_protocol.plugins import (
    PluginCancel,
    PluginError,
    PluginEvent,
    PluginRequest,
    PluginResult,
)
from racp_protocol.registry import validate_payload

from racp_agent.plugins.gdb_driver import GDBDriver
from racp_agent.plugins.manifest import decode_frame, encode_frame
from racp_agent.providers.shell import execution_env


def target_metadata(path: Path) -> tuple[str, str, str]:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        stream.seek(0)
        header = stream.read(65536)
    if header.startswith(b"MZ"):
        offset = struct.unpack_from("<I", header, 60)[0]
        if header[offset : offset + 4] != b"PE\0\0":
            raise ValueError("invalid PE target")
        machine = struct.unpack_from("<H", header, offset + 4)[0]
        optional = offset + 24
        magic = struct.unpack_from("<H", header, optional)[0]
        base = struct.unpack_from(
            "<Q" if magic == 0x20B else "<I", header, optional + (24 if magic == 0x20B else 28)
        )[0]
        architecture = {0x8664: "x86_64", 0x14C: "x86", 0xAA64: "aarch64"}.get(machine, "unknown")
        return digest, architecture, hex(base)
    if header.startswith(b"\x7fELF"):
        order = "<" if header[5] == 1 else ">"
        machine = struct.unpack_from(order + "H", header, 18)[0]
        return digest, {62: "x86_64", 3: "x86", 183: "aarch64"}.get(machine, "unknown"), "0x0"
    raise RACPError("OPERATION_NOT_SUPPORTED", "GDB adapter requires PE/ELF target", layer="plugin")


class GDBPlugin:
    def __init__(self, manifest: Path, executable: Path, executable_sha256: str) -> None:
        self.manifest, self.executable = manifest, executable
        self.executable_sha256 = executable_sha256
        self.instance: str | None = None
        self.sequence = 0
        self.sessions: dict[str, GDBDriver] = {}
        self.active: set[str] = set()
        self.version: str | None = None

    def verify_backend(self) -> None:
        with self.executable.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != self.executable_sha256:
            raise RACPError(
                "PLUGIN_VERSION_MISMATCH", "approved GDB executable changed", layer="plugin"
            )

    def publish(self, resource: str, data: dict[str, Any]) -> None:
        if self.instance is None or resource not in self.active:
            return
        self.sequence += 1
        state = data["debugger_state"]
        message = PluginEvent(
            instance_id=self.instance,
            sequence=str(self.sequence),
            kind="debugger.stopped"
            if state == "STOPPED"
            else "debugger.exited"
            if state == "EXITED"
            else "debugger.running",
            data={"resource_id": resource, **data},
        )
        sys.stdout.buffer.write(encode_frame(message))
        sys.stdout.buffer.flush()

    async def health(self) -> dict[str, Any]:
        await asyncio.to_thread(self.verify_backend)
        if self.version is None:
            process = await asyncio.create_subprocess_exec(
                str(self.executable),
                "--version",
                env=execution_env({}),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), 5)
                match = re.search(rb"\b(\d+\.\d+(?:\.\d+)?)\s*$", stdout.splitlines()[0])
                if process.returncode or match is None or len(stdout) > 65536:
                    raise ValueError("installed GDB version unavailable")
                self.version = match[1].decode()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        return {
            "manifest_sha256": hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            "backend_version": self.version,
        }

    async def execute(self, request: PluginRequest) -> dict[str, Any]:
        if self.instance is None:
            self.instance = request.instance_id
        elif self.instance != request.instance_id:
            raise RACPError("HANDLE_EXPIRED", "plugin instance changed", layer="plugin")
        if request.operation == "plugin.health":
            return await self.health()
        payload = validate_payload(request.operation, request.payload)
        timeout = max(0.001, (request.deadline_unix_ms / 1000) - time.time())
        if request.operation == "debugger.launch":
            await asyncio.to_thread(self.verify_backend)
            if len(self.sessions) >= 4:
                raise RACPError("RESOURCE_EXHAUSTED", "GDB session limit reached", layer="plugin")
            target = Path(payload["executable"])
            digest, architecture, base = await asyncio.to_thread(target_metadata, target)
            resource = new_id("gdb")
            driver = await GDBDriver.start(
                self.executable, target.parent, lambda data: self.publish(resource, data)
            )
            self.sessions[resource] = driver
            await driver.launch(target, payload["args"], timeout)
            if driver.pid is None or driver.birth is None:
                raise RACPError(
                    "GDB_TRANSPORT_FAILED",
                    "GDB target identity absent",
                    layer="plugin",
                    execution_state="unknown",
                )
            self.active.add(resource)
            return {
                "resource_id": resource,
                "target_sha256": digest,
                "architecture": architecture,
                "image_base": base,
                "debugger_state": driver.state,
                "pid": driver.pid,
                "create_time": driver.birth,
                "stop_sequence": str(driver.sequence),
                "stop_reason": driver.reason,
            }
        found = self.sessions.get(payload.get("debug_id", ""))
        if found is None:
            raise RACPError("HANDLE_EXPIRED", "GDB inferior not found", layer="plugin")
        driver = found
        operation = request.operation
        if operation == "debugger.info":
            return {
                "debugger_state": driver.state,
                "stop_sequence": str(driver.sequence),
                "stop_reason": driver.reason,
                "utf8_arguments_supported": driver.utf8,
            }
        if operation == "debugger.close":
            self.active.discard(payload["debug_id"])
            await driver.close()
            del self.sessions[payload["debug_id"]]
            return {"closed": True, "debugger_state": "EXITED"}
        if (
            operation in {"debugger.registers", "debugger.read_memory", "debugger.backtrace"}
            and driver.state != "STOPPED"
        ):
            raise RACPError("PRECONDITION_FAILED", "GDB inferior must be STOPPED", layer="plugin")
        if operation == "debugger.command":
            action = payload["action"]
            if action in {"continue", "step_into", "step_over", "interrupt"}:
                command = {
                    "continue": "-exec-continue",
                    "step_into": "-exec-step",
                    "step_over": "-exec-next",
                    "interrupt": "-exec-interrupt --all",
                }[action]
                await driver.command(command, timeout)
                return {
                    "accepted": True,
                    "debugger_state": "RUNNING" if action != "interrupt" else driver.state,
                }
            if action == "set_breakpoint":
                location = payload["symbol"] or ("*" + payload["address"])
                result = await driver.command("-break-insert " + location, timeout)
                return {
                    "breakpoint_id": "bp_" + result["bkpt"]["number"],
                    "debugger_state": driver.state,
                }
            number = payload["breakpoint_id"].removeprefix("bp_")
            if not re.fullmatch(r"[0-9]+", number):
                raise RACPError("INVALID_ARGUMENT", "invalid GDB breakpoint ID", layer="plugin")
            await driver.command("-break-delete " + number, timeout)
            return {"removed": True, "debugger_state": driver.state}
        if operation == "debugger.registers":
            names = (await driver.command("-data-list-register-names", timeout))["register-names"]
            scalar = {
                "rip",
                "rsp",
                "rbp",
                "rax",
                "rbx",
                "rcx",
                "rdx",
                "rsi",
                "rdi",
                "eflags",
                "eip",
                "esp",
                "ebp",
                "eax",
                "ebx",
                "ecx",
                "edx",
                "esi",
                "edi",
                *["r" + str(i) for i in range(8, 16)],
            }
            numbers = [i for i, name in enumerate(names) if name in scalar]
            values = (
                await driver.command(
                    "-data-list-register-values x " + " ".join(map(str, numbers)), timeout
                )
            )["register-values"]
            return {"registers": {names[int(item["number"])]: item["value"] for item in values}}
        if operation == "debugger.read_memory":
            if payload["size_bytes"] > 16384:
                raise RACPError(
                    "INVALID_ARGUMENT", "adapter memory chunk limit is 16 KiB", layer="plugin"
                )
            result = await driver.command(
                f"-data-read-memory-bytes {payload['address']} {payload['size_bytes']}", timeout
            )
            return {"bytes_hex": "".join(row["contents"] for row in result["memory"])}
        if operation == "debugger.backtrace":
            result = await driver.command("-stack-list-frames 0 63", timeout)
            return {"frames": [row.get("frame", row) for row in result["stack"]]}
        raise RACPError(
            "OPERATION_NOT_SUPPORTED", "GDB adapter operation unsupported", layer="plugin"
        )

    async def run(self) -> None:
        try:
            while raw := await asyncio.to_thread(sys.stdin.buffer.readline, 1024 * 1024 + 1):
                request = decode_frame(raw)
                if isinstance(request, PluginCancel):
                    continue  # Active call cancellation is enforced by parent-owned Job/group.
                if not isinstance(request, PluginRequest):
                    raise ValueError("plugin stdin is request/cancel only")
                try:
                    async with asyncio.timeout(
                        max(0.001, request.deadline_unix_ms / 1000 - time.time())
                    ):
                        value = await self.execute(request)
                    reply = PluginResult(
                        instance_id=request.instance_id,
                        request_id=request.request_id,
                        state="SUCCEEDED",
                        result=value,
                    )
                except RACPError as exc:
                    reply = PluginResult(
                        instance_id=request.instance_id,
                        request_id=request.request_id,
                        state="FAILED",
                        error=PluginError.model_validate(
                            {
                                "code": exc.error.code,
                                "message": exc.error.message,
                                "execution_state": exc.error.execution_state,
                            }
                        ),
                    )
                sys.stdout.buffer.write(encode_frame(reply))
                sys.stdout.buffer.flush()
        finally:
            await asyncio.gather(
                *(driver.close() for driver in self.sessions.values()), return_exceptions=True
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Local allowlisted RACP GDB/MI plugin")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gdb", type=Path, required=True)
    parser.add_argument("--gdb-sha256", required=True)
    args = parser.parse_args()
    if not args.manifest.is_absolute() or not args.gdb.is_absolute():
        raise ValueError("GDB plugin paths must be absolute")
    if not re.fullmatch(r"[a-f0-9]{64}", args.gdb_sha256):
        raise ValueError("GDB executable hash must be SHA-256")
    asyncio.run(GDBPlugin(args.manifest, args.gdb, args.gdb_sha256).run())


if __name__ == "__main__":
    main()
