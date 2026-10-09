"""Bounded CF_UNICODETEXT access in the calling Broker's window station.

The Broker's session/desktop guard is the access boundary. This module owns a
hidden message window so EmptyClipboard/SetClipboardData have a valid owner.
"""

import ctypes
import hashlib
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from typing import Any, Self


class ClipboardChanged(Exception):
    pass


class ClipboardBusy(Exception):
    pass


class InvalidClipboardData(Exception):
    pass


class WindowsClipboard:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows clipboard required")
        native: Any = ctypes
        self.user = native.WinDLL("user32", use_last_error=True)
        self.kernel = native.WinDLL("kernel32", use_last_error=True)
        self.user.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        self.user.CreateWindowExW.restype = wintypes.HWND
        self.user.DestroyWindow.argtypes = [wintypes.HWND]
        self.user.DestroyWindow.restype = wintypes.BOOL
        self.user.OpenClipboard.argtypes = [wintypes.HWND]
        self.user.OpenClipboard.restype = wintypes.BOOL
        self.user.CloseClipboard.restype = wintypes.BOOL
        self.user.GetClipboardSequenceNumber.restype = wintypes.DWORD
        self.user.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        self.user.IsClipboardFormatAvailable.restype = wintypes.BOOL
        self.user.GetClipboardData.argtypes = [wintypes.UINT]
        self.user.GetClipboardData.restype = wintypes.HANDLE
        self.user.EmptyClipboard.restype = wintypes.BOOL
        self.user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user.SetClipboardData.restype = wintypes.HANDLE
        self.kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalSize.restype = ctypes.c_size_t
        self.kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalLock.restype = ctypes.c_void_p
        self.kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalUnlock.restype = wintypes.BOOL
        self.kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        self.kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalFree.restype = wintypes.HGLOBAL
        self.window = self.user.CreateWindowExW(
            0,
            "STATIC",
            "RACP clipboard owner",
            0,
            0,
            0,
            0,
            0,
            ctypes.c_void_p(-3),
            None,
            None,
            None,
        )
        if not self.window:
            raise native.WinError(ctypes.get_last_error())

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.window:
            if not self.user.DestroyWindow(self.window):
                native: Any = ctypes
                raise native.WinError(ctypes.get_last_error())
            self.window = None

    @contextmanager
    def opened(self) -> Iterator[None]:
        if not self.user.OpenClipboard(self.window):
            raise ClipboardBusy("Clipboard is in use or access is denied")
        try:
            yield
        finally:
            if not self.user.CloseClipboard():
                native: Any = ctypes
                raise native.WinError(ctypes.get_last_error())

    def state(self) -> dict[str, Any]:
        with self.opened():
            return {"sequence_number": self.user.GetClipboardSequenceNumber()}

    def read(self, max_bytes: int) -> dict[str, Any]:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 8192:
            raise ValueError("Invalid clipboard budget")
        with self.opened():
            if not self.user.IsClipboardFormatAvailable(13):
                return {
                    "text": None,
                    "sequence_number": self.user.GetClipboardSequenceNumber(),
                    "returned_bytes": 0,
                    "truncated": False,
                    "format": "CF_UNICODETEXT",
                }
            handle = self.user.GetClipboardData(13)
            size = self.kernel.GlobalSize(handle) if handle else 0
            pointer = self.kernel.GlobalLock(handle) if handle else None
            if not pointer:
                raise InvalidClipboardData("Clipboard has no readable Unicode allocation")
            try:
                raw = ctypes.string_at(pointer, min(size, 2 * (max_bytes + 1)))
            finally:
                self.kernel.GlobalUnlock(handle)
            end = next((i for i in range(0, len(raw) - 1, 2) if raw[i : i + 2] == b"\0\0"), None)
            capped = end is None and len(raw) < size
            if end is None and not capped:
                raise InvalidClipboardData("Clipboard Unicode text is not terminated")
            raw = raw[:end] if end is not None else raw
            if capped and len(raw) >= 2 and 0xD800 <= int.from_bytes(raw[-2:], "little") <= 0xDBFF:
                raw = raw[:-2]
            try:
                encoded = raw.decode("utf-16-le").encode("utf-8")
            except UnicodeError:
                raise InvalidClipboardData("Clipboard Unicode text is malformed") from None
            text = encoded[:max_bytes].decode("utf-8", errors="ignore")
            returned = text.encode("utf-8")
            return {
                "text": text,
                "sequence_number": self.user.GetClipboardSequenceNumber(),
                "returned_bytes": len(returned),
                "storage_bytes": size,
                "truncated": capped or len(encoded) > max_bytes,
                "returned_sha256": hashlib.sha256(returned).hexdigest(),
                "format": "CF_UNICODETEXT",
            }

    def write(
        self, text: str | None, expected_sequence: int, *, guard: Callable[[], None] = lambda: None
    ) -> dict[str, Any]:
        if type(expected_sequence) is not int or not 0 <= expected_sequence <= 0xFFFFFFFF:
            raise ValueError("Invalid clipboard sequence")
        encoded = text.encode("utf-8") if text is not None else b""
        if len(encoded) > 8192 or (text is not None and "\0" in text):
            raise ValueError("Invalid clipboard text")
        memory = None
        transferred = False
        if text is not None:
            data = text.encode("utf-16-le") + b"\0\0"
            memory = self.kernel.GlobalAlloc(0x42, len(data))  # MOVEABLE | ZEROINIT
            if not memory:
                raise MemoryError("Clipboard allocation failed")
            pointer = self.kernel.GlobalLock(memory)
            if not pointer:
                self.kernel.GlobalFree(memory)
                raise MemoryError("Clipboard allocation lock failed")
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                self.kernel.GlobalUnlock(memory)
        try:
            with self.opened():
                if self.user.GetClipboardSequenceNumber() != expected_sequence:
                    raise ClipboardChanged("Clipboard changed; read its current sequence first")
                guard()
                native: Any = ctypes
                if not self.user.EmptyClipboard():
                    raise native.WinError(ctypes.get_last_error())
                if memory is not None:
                    if not self.user.SetClipboardData(13, memory):
                        raise native.WinError(ctypes.get_last_error())
                    transferred = True
            # CloseClipboard may advance the serial again. This is an observation,
            # not an assertion that no other application has changed the clipboard.
            return {
                "sequence_number": self.user.GetClipboardSequenceNumber(),
                "previous_sequence": expected_sequence,
                "written_bytes": len(encoded),
                "written_sha256": hashlib.sha256(encoded).hexdigest(),
                "cleared": text is None,
                "read_before_next_write": True,
            }
        finally:
            if memory is not None and not transferred:
                self.kernel.GlobalFree(memory)
