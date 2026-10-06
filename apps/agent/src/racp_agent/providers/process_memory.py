"""Windows process observations collected on B and returned to A as bytes/Artifacts."""

import asyncio
import ctypes
import hashlib
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

import psutil
from racp_agent.providers.filesystem import Budget
from racp_agent.providers.paths import PathGuard
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import timestamp

windows_ctypes: Any = ctypes


class MemoryInformation(ctypes.Structure):
    _fields_ = [
        ("base", ctypes.c_void_p),
        ("allocation_base", ctypes.c_void_p),
        ("allocation_protection", wintypes.DWORD),
        ("partition", wintypes.WORD),
        ("size", ctypes.c_size_t),
        ("state", wintypes.DWORD),
        ("protection", wintypes.DWORD),
        ("kind", wintypes.DWORD),
    ]


class WindowsProcessMemory:
    def __init__(self, pid: int, created: float) -> None:
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "Memory collection requires Windows x64 Agent",
                layer="provider",
            )
        import win32api

        self.kernel = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.VirtualQueryEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(MemoryInformation),
            ctypes.c_size_t,
        ]
        self.kernel.VirtualQueryEx.restype = ctypes.c_size_t
        self.kernel.ReadProcessMemory.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.kernel.ReadProcessMemory.restype = wintypes.BOOL
        try:
            # QUERY_INFORMATION | VM_READ | SYNCHRONIZE; no write/debug/elevation rights.
            self.handle = win32api.OpenProcess(0x100410, False, pid)
        except Exception:
            raise RACPError(
                "PERMISSION_DENIED", "Windows denied process memory access", layer="provider"
            ) from None
        try:
            self.alive()
            if abs(psutil.Process(pid).create_time() - created) > 0.000001:
                raise RACPError("PRECONDITION_FAILED", "PID identity changed", layer="provider")
        except BaseException:
            self.handle.Close()
            raise

    def alive(self) -> None:
        import win32event

        if win32event.WaitForSingleObject(self.handle, 0) == 0:
            raise RACPError(
                "PROCESS_NOT_FOUND", "Process exited during memory observation", layer="provider"
            )

    def close(self) -> None:
        self.handle.Close()

    def region(self, address: int) -> dict[str, Any] | None:
        self.alive()
        info = MemoryInformation()
        count = self.kernel.VirtualQueryEx(
            int(self.handle), address, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not count:
            if windows_ctypes.get_last_error() == 87:
                return None
            raise RACPError(
                "MEMORY_UNAVAILABLE", "Windows memory region query failed", layer="provider"
            )
        if count != ctypes.sizeof(info) or not info.size:
            raise RACPError("MEMORY_UNAVAILABLE", "Invalid Windows memory region", layer="provider")
        readable = (
            info.state == 0x1000
            and not info.protection & 0x100
            and (info.protection & 0xFF) in {2, 4, 8, 0x20, 0x40, 0x80}
        )
        return {
            "base_address": hex(info.base or 0),
            "size_bytes": int(info.size),
            "allocation_base": hex(info.allocation_base or 0),
            "state": int(info.state),
            "protection": int(info.protection),
            "type": int(info.kind),
            "readable": readable,
        }

    def read(self, address: int, size: int) -> bytes:
        self.alive()
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if (
            not self.kernel.ReadProcessMemory(
                int(self.handle), address, buffer, size, ctypes.byref(count)
            )
            or count.value != size
        ):
            raise RACPError(
                "MEMORY_UNAVAILABLE",
                "Requested memory is inaccessible or changed; no partial result published",
                layer="provider",
            )
        self.alive()
        return buffer.raw


def collect(
    operation: str,
    payload: dict[str, Any],
    context: ExecutionContext,
    spool: Path,
    cancelled: threading.Event,
) -> dict[str, Any]:
    budget = Budget(time.monotonic() + context.timeout_ms / 1000, cancelled)
    budget.check()
    target = WindowsProcessMemory(payload["pid"], payload["create_time"])
    path = spool / (context.operation_id + ".process-memory")
    try:
        identity = {
            "pid": payload["pid"],
            "create_time": payload["create_time"],
            "agent_boot_id": context.agent_boot_id,
            "observed_at": timestamp(),
            "consistency": "live_process_observation",
            "atomic_snapshot": False,
        }
        if operation == "process.memory_regions":
            address = int(payload["start_address"], 16)
            items = []
            finished = False
            for _ in range(payload["limit"]):
                budget.check()
                region = target.region(address)
                if region is None:
                    finished = True
                    break
                next_address = int(region["base_address"], 16) + region["size_bytes"]
                if next_address <= address or next_address >= 2**64:
                    finished = True
                    break
                items.append(region)
                address = next_address
            return {**identity, "items": items, "next_address": None if finished else hex(address)}
        address, size = int(payload["address"], 16), payload["size_bytes"]
        result = {**identity, "address": hex(address), "size_bytes": size}
        if size <= 4096:
            data = target.read(address, size)
            budget.check()
            return {**result, "bytes_hex": data.hex(), "sha256": hashlib.sha256(data).hexdigest()}
        guard = PathGuard(spool)
        digest = hashlib.sha256()
        with guard.parent(path) as parent:
            created = False
            try:
                descriptor = parent.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                created = True
                with os.fdopen(descriptor, "wb") as output:
                    offset = 0
                    while offset < size:
                        budget.check()
                        data = target.read(address + offset, min(256 * 1024, size - offset))
                        output.write(data)
                        digest.update(data)
                        offset += len(data)
                    output.flush()
                    os.fsync(output.fileno())
                budget.check()
            except BaseException:
                if created:
                    parent.unlink(path.name)
                raise
        return {
            **result,
            "sha256": digest.hexdigest(),
            "artifact_id": None,
            "spool_path": str(path),
            "artifact_media_type": "application/octet-stream",
        }
    finally:
        target.close()


async def execute(
    operation: str,
    payload: dict[str, Any],
    context: ExecutionContext,
    spool: Path,
) -> dict[str, Any]:
    cancelled = threading.Event()
    work = asyncio.create_task(
        asyncio.to_thread(collect, operation, payload, context, spool, cancelled)
    )
    try:
        result = await asyncio.shield(work)
    except asyncio.CancelledError:
        cancelled.set()
        # Preserve a completed observation if cancellation arrived after publication.
        # An unfinished worker sees the flag, deletes its owned partial file and raises.
        result = await asyncio.shield(work)
    return {"state": "SUCCEEDED", "result": result, "error": None}
