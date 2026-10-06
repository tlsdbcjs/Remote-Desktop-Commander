"""Raw ConPTY byte transport; assign a suspended child to a Job before it runs."""

import ctypes
import subprocess
from ctypes import wintypes as w
from typing import Any

import win32job

windows_ctypes: Any = ctypes


class Coord(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("xsize", w.DWORD),
        ("ysize", w.DWORD),
        ("xchars", w.DWORD),
        ("ychars", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_bytes", ctypes.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", w.HANDLE),
        ("thread", w.HANDLE),
        ("pid", w.DWORD),
        ("tid", w.DWORD),
    ]


class WindowsTerminal:
    def __init__(
        self,
        argv: list[str],
        cwd: str,
        env: dict[str, str],
        cols: int,
        rows: int,
    ) -> None:
        self.kernel: Any = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p

        def bind(name: str, args: list[Any], returns: Any) -> None:
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, returns

        bind("CreatePipe", [pointer, pointer, pointer, w.DWORD], w.BOOL)
        bind("CloseHandle", [w.HANDLE], w.BOOL)
        bind("CreatePseudoConsole", [Coord, w.HANDLE, w.HANDLE, w.DWORD, pointer], ctypes.c_long)
        bind("ResizePseudoConsole", [w.HANDLE, Coord], ctypes.c_long)
        bind("ClosePseudoConsole", [w.HANDLE], None)
        bind("InitializeProcThreadAttributeList", [pointer, w.DWORD, w.DWORD, pointer], w.BOOL)
        bind(
            "UpdateProcThreadAttribute",
            [pointer, w.DWORD, ctypes.c_size_t, pointer, ctypes.c_size_t, pointer, pointer],
            w.BOOL,
        )
        bind("DeleteProcThreadAttributeList", [pointer], None)
        bind(
            "CreateProcessW",
            [
                w.LPCWSTR,
                w.LPWSTR,
                pointer,
                pointer,
                w.BOOL,
                w.DWORD,
                pointer,
                w.LPCWSTR,
                pointer,
                pointer,
            ],
            w.BOOL,
        )
        bind("ResumeThread", [w.HANDLE], w.DWORD)
        bind("TerminateProcess", [w.HANDLE, w.UINT], w.BOOL)
        bind("PeekNamedPipe", [w.HANDLE, pointer, w.DWORD, pointer, pointer, pointer], w.BOOL)
        bind("ReadFile", [w.HANDLE, pointer, w.DWORD, pointer, pointer], w.BOOL)
        bind("WriteFile", [w.HANDLE, pointer, w.DWORD, pointer, pointer], w.BOOL)
        bind("GetExitCodeProcess", [w.HANDLE, pointer], w.BOOL)
        bind("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)
        self.input, self.output, self.console = w.HANDLE(), w.HANDLE(), w.HANDLE()
        input_read, output_write = w.HANDLE(), w.HANDLE()
        self.process = ProcessInfo()
        self.job: Any = None
        initialized = False
        attributes: Any = None
        try:
            self.check(
                self.kernel.CreatePipe(
                    ctypes.byref(input_read), ctypes.byref(self.input), None, 65536
                )
            )
            self.check(
                self.kernel.CreatePipe(
                    ctypes.byref(self.output), ctypes.byref(output_write), None, 65536
                )
            )
            self.hresult(
                self.kernel.CreatePseudoConsole(
                    Coord(cols, rows), input_read, output_write, 0, ctypes.byref(self.console)
                )
            )
            size = ctypes.c_size_t()
            self.kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            self.check(
                self.kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size))
            )
            initialized = True
            self.check(
                self.kernel.UpdateProcThreadAttribute(
                    attributes, 0, 0x20016, self.console, ctypes.sizeof(w.HANDLE), None, None
                )
            )
            startup = StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            # Explicit null standard handles prevent Windows from duplicating
            # redirected parent I/O into the new pseudoconsole child.
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = ctypes.create_unicode_buffer(
                "\0".join(
                    f"{key}={value}"
                    for key, value in sorted(env.items(), key=lambda item: item[0].upper())
                )
                + "\0\0"
            )
            self.check(
                self.kernel.CreateProcessW(
                    argv[0],
                    command,
                    None,
                    None,
                    False,
                    0x80000 | 0x400 | 0x4,
                    environment,
                    cwd,
                    ctypes.byref(startup),
                    ctypes.byref(self.process),
                )
            )
            self.pid = int(self.process.pid)
            self.job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] = (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation, info
            )
            win32job.AssignProcessToJobObject(self.job, self.process.process)
            if self.kernel.ResumeThread(self.process.thread) == 0xFFFFFFFF:
                self.check(False)
        except BaseException:
            if self.process.process:
                self.kernel.TerminateProcess(self.process.process, 1)
            if self.job is not None:
                self.job.Close()
                self.job = None
            # Close the receiving pipe before ClosePseudoConsole on failed setup;
            # no reader exists yet to drain the teardown frame.
            self.close_handle(self.output)
            if self.console:
                self.kernel.ClosePseudoConsole(self.console)
                self.console = w.HANDLE()
            self.close_handle(self.input)
            self.close_handle(self.process.process)
            raise
        finally:
            if initialized:
                self.kernel.DeleteProcThreadAttributeList(attributes)
            self.close_handle(input_read)
            self.close_handle(output_write)
            self.close_handle(self.process.thread)

    @staticmethod
    def check(success: Any) -> None:
        if not success:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())

    @staticmethod
    def hresult(code: int) -> None:
        if code < 0:
            raise OSError(f"ConPTY HRESULT 0x{code & 0xFFFFFFFF:08x}")

    def close_handle(self, handle: Any) -> None:
        if handle:
            self.kernel.CloseHandle(handle)

    def read(self) -> bytes | None:
        available = w.DWORD()
        if not self.kernel.PeekNamedPipe(self.output, None, 0, None, ctypes.byref(available), None):
            if windows_ctypes.get_last_error() in {109, 232}:
                return b""
            self.check(False)
        if not available.value:
            return None
        buffer = ctypes.create_string_buffer(min(available.value, 65536))
        read = w.DWORD()
        self.check(self.kernel.ReadFile(self.output, buffer, len(buffer), ctypes.byref(read), None))
        return buffer.raw[: read.value]

    def write(self, data: bytes) -> int:
        written = w.DWORD()
        self.check(self.kernel.WriteFile(self.input, data, len(data), ctypes.byref(written), None))
        return int(written.value)

    def resize(self, cols: int, rows: int) -> None:
        self.hresult(self.kernel.ResizePseudoConsole(self.console, Coord(cols, rows)))

    def poll(self) -> int | None:
        status = self.kernel.WaitForSingleObject(self.process.process, 0)
        if status == 258:  # WAIT_TIMEOUT
            return None
        if status != 0:  # WAIT_OBJECT_0
            self.check(False)
        code = w.DWORD()
        self.check(self.kernel.GetExitCodeProcess(self.process.process, ctypes.byref(code)))
        return int(code.value)

    def terminate(self) -> None:
        if self.job is not None:
            win32job.TerminateJobObject(self.job, 1)

    def finish(self) -> None:
        self.terminate()
        if self.console:
            self.kernel.ClosePseudoConsole(self.console)
            self.console = w.HANDLE()

    def close(self) -> None:
        if self.job is not None:
            self.job.Close()
            self.job = None
        self.close_handle(self.input)
        self.close_handle(self.output)
        self.close_handle(self.process.process)
