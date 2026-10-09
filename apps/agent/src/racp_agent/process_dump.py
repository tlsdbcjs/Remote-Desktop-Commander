"""Stdlib-only Windows DbgHelp worker, run by the Agent's contained recipe.

IoStartCallback(S_FALSE) directs every write through the bounded writer.
No debugger service, privilege elevation, arbitrary flags, or target termination.
"""

import argparse
import ctypes
import hashlib
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, BinaryIO


class DumpBudgetExceeded(Exception):
    pass


class DumpIdentityChanged(Exception):
    pass


def failure_code(error: Exception) -> str:
    if isinstance(error, DumpBudgetExceeded):
        return "RESOURCE_EXHAUSTED"
    if isinstance(error, DumpIdentityChanged):
        return "PRECONDITION_FAILED"
    winerror = getattr(error, "winerror", None)
    if winerror is not None and winerror & 0xFFFFFFFF in {5, 0x80070005}:
        return "PERMISSION_DENIED"
    return "CAPABILITY_UNAVAILABLE"


class BudgetedDumpWriter:
    def __init__(self, stream: BinaryIO, limit: int) -> None:
        self.stream = stream
        self.limit = limit
        self.size = 0

    def write(self, offset: int, data: bytes) -> None:
        if offset < 0 or offset + len(data) > self.limit:
            raise DumpBudgetExceeded("Dump exceeds byte budget")
        self.stream.seek(offset)
        position = 0
        while position < len(data):
            count = self.stream.write(data[position:])
            if not count:
                raise OSError("Incomplete dump write")
            position += count
        self.size = max(self.size, offset + len(data))


class IoCallback(ctypes.Structure):
    _pack_ = 4  # minidumpapiset.h uses pshpack4.h, including on x64
    _fields_ = [
        ("handle", wintypes.HANDLE),
        ("offset", ctypes.c_uint64),
        ("buffer", ctypes.c_void_p),
        ("size", wintypes.ULONG),
    ]


class CallbackInput(ctypes.Structure):
    _pack_ = 4
    # Only IoStart/IoWriteAll/IoFinish interpret this union member.
    _fields_ = [
        ("pid", wintypes.DWORD),
        ("process", wintypes.HANDLE),
        ("kind", wintypes.ULONG),
        ("io", IoCallback),
    ]


def collect(pid: int, created: float, mode: str, path: Path, limit: int) -> dict[str, Any]:
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise NotImplementedError("Windows x64 DbgHelp required")
    import msvcrt

    native: Any = ctypes
    kernel = native.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    directory = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(directory, len(directory))
    if not 0 < length < len(directory):
        raise native.WinError(ctypes.get_last_error())
    # Resolve the OS DLL explicitly; never search the current directory.
    dbghelp = native.WinDLL(str(Path(directory.value) / "dbghelp.dll"), use_last_error=True)
    handle = kernel.OpenProcess(0x100410, False, pid)  # query/read/synchronize only
    if not handle:
        raise native.WinError(ctypes.get_last_error())
    created_output = False
    complete = False
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise native.WinError(ctypes.get_last_error())
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        actual = (ticks - 116444736000000000) / 1e7
        if abs(actual - created) > 1e-6 or kernel.WaitForSingleObject(handle, 0) != 258:
            raise DumpIdentityChanged("Target identity changed or exited")
        with path.open("x+b") as stream:
            created_output = True
            writer = BudgetedDumpWriter(stream, limit)
            failure: list[Exception] = []
            started = False
            finished = False
            callback_type = native.WINFUNCTYPE(
                wintypes.BOOL,
                ctypes.c_void_p,
                ctypes.POINTER(CallbackInput),
                ctypes.c_void_p,
            )

            def callback(_parameter: Any, input_pointer: Any, output_pointer: Any) -> bool:
                nonlocal started, finished
                incoming = input_pointer.contents
                status = ctypes.cast(output_pointer, ctypes.POINTER(ctypes.c_int32))
                try:
                    if incoming.kind == 11:  # IoStartCallback
                        started = True
                        status[0] = 1  # S_FALSE: callbacks own all file I/O
                    elif incoming.kind == 12:  # IoWriteAllCallback
                        if not started or incoming.io.offset + incoming.io.size > limit:
                            raise DumpBudgetExceeded("Dump exceeds byte budget")
                        writer.write(
                            incoming.io.offset,
                            ctypes.string_at(incoming.io.buffer, incoming.io.size),
                        )
                        status[0] = 0  # S_OK
                    elif incoming.kind == 13:  # IoFinishCallback
                        finished = True
                        status[0] = 0
                    return True
                except Exception as exc:
                    failure.append(exc)
                    status[0] = -2147467259  # E_FAIL; never let Python escape a C callback
                    return False

            callback_function = callback_type(callback)

            class CallbackInformation(ctypes.Structure):
                _pack_ = 4
                _fields_ = [("routine", callback_type), ("parameter", ctypes.c_void_p)]

            info = CallbackInformation(callback_function, None)
            dbghelp.MiniDumpWriteDump.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HANDLE,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(CallbackInformation),
            ]
            dbghelp.MiniDumpWriteDump.restype = wintypes.BOOL
            # DbgHelp still requires a valid hFile with alternate I/O. A device sink
            # prevents any fallback implementation from writing unbounded spool bytes.
            with open(r"\\.\NUL", "wb") as sink:
                succeeded = dbghelp.MiniDumpWriteDump(
                    handle,
                    pid,
                    msvcrt.get_osfhandle(sink.fileno()),
                    2 if mode == "full" else 0,
                    None,
                    None,
                    ctypes.byref(info),
                )
            if failure:
                raise failure[0]
            if not succeeded:
                raise native.WinError(ctypes.get_last_error())
            if not started or not finished or writer.size < 32:
                raise OSError("Incomplete DbgHelp I/O receipt")
            if kernel.WaitForSingleObject(handle, 0) != 258:
                raise DumpIdentityChanged("Target exited during collection")
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            receipt = {
                "status": "SUCCEEDED",
                "backend": "Windows DbgHelp MiniDumpWriteDump",
                "target_pid": pid,
                "target_create_time": actual,
                "mode": mode,
                "bytes": writer.size,
                "sha256": digest,
                "complete": True,
                "process_handle_closed": True,
                "bounded_io": True,
            }
        complete = True
        return receipt
    finally:
        kernel.CloseHandle(handle)
        if created_output and not complete:
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--create-time", type=float, required=True)
    parser.add_argument("--mode", choices=["mini", "full"], required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Defense in depth for the fixed recipe. It is not an exposed remote shell tool.
    if not 0 < args.pid <= 0xFFFFFFFF or not 4096 <= args.max_bytes <= 256 * 1024**2:
        parser.error("Invalid target or budget")
    try:
        receipt = collect(args.pid, args.create_time, args.mode, args.output, args.max_bytes)
    except Exception as exc:
        code = failure_code(exc)
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "code": code,
                    "reason": type(exc).__name__,
                    "winerror": getattr(exc, "winerror", None),
                }
            )
        )
        return 1
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
