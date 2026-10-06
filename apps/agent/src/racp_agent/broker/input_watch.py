"""Fast desktop hooks: interruption counters only, never keys, text, or pointer history."""

import ctypes
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from racp_domain.models import RACPError

windows_ctypes: Any = ctypes


class KeyboardEvent(ctypes.Structure):
    _fields_ = [
        ("vk", wintypes.DWORD),
        ("scan", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class MouseEvent(ctypes.Structure):
    _fields_ = [
        ("point", wintypes.POINT),
        ("data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class InterruptionCounter:
    def __init__(self, tag: int) -> None:
        self.tag, self.sequence, self.own_sequence = tag, 0, 0
        self.foreground_sequence = 0
        self.mouse_events = self.mouse_marked = self.mouse_injected = 0
        self.serial = 0
        self.windows: dict[int, int] = {}

    def input(self, flags: int, extra: int, injected_flag: int) -> None:
        if flags & injected_flag and extra == self.tag:
            self.own_sequence += 1
        else:
            self.sequence += 1

    def generation(self, handle: int) -> int:
        if handle not in self.windows:
            if len(self.windows) >= 4096:
                self.windows.clear()  # All old tokens fail closed after bounded eviction.
            self.serial += 1
            self.windows[handle] = self.serial
        return self.windows[handle]

    def window_event(self, event: int, handle: int, object_id: int, child_id: int) -> None:
        if event == 3:  # EVENT_SYSTEM_FOREGROUND, including change-away-and-back.
            self.foreground_sequence += 1
        elif event in {0x8000, 0x8001} and object_id == 0 and child_id == 0:
            if handle in self.windows:
                self.serial += 1
                self.windows[handle] = self.serial


class InputWatch:
    def __init__(
        self,
        tag: int,
        *,
        own_keyboard: Callable[[int, int, int], None] | None = None,
        own_mouse: Callable[[int, int], None] | None = None,
    ) -> None:
        self.counter = InterruptionCounter(tag)
        self.own_keyboard, self.own_mouse = own_keyboard, own_mouse
        self.ready, self.stopped, self.fence = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.error: BaseException | None = None
        self.thread_id = 0
        self.heartbeat = 0.0
        self.thread = threading.Thread(target=self.run, name="racp-input-watch", daemon=True)
        self.thread.start()
        if not self.ready.wait(2) or self.error is not None:
            self.close()
            raise RACPError("SESSION_UNAVAILABLE", "desktop hooks unavailable", layer="broker")

    def healthy(self) -> bool:
        return (
            self.thread.is_alive()
            and self.error is None
            and not self.stopped.is_set()
            and time.monotonic() - self.heartbeat < 2
        )

    def sync(self) -> None:
        if not self.healthy():
            raise RACPError("SESSION_UNAVAILABLE", "desktop hook pump unavailable", layer="broker")
        self.fence.clear()
        if not self.user.PostThreadMessageW(self.thread_id, 0x8001, 0, 0) or not self.fence.wait(1):
            raise RACPError("SESSION_UNAVAILABLE", "desktop hook fence unavailable", layer="broker")

    def close(self) -> None:
        self.stopped.set()
        if self.thread_id:
            self.user.PostThreadMessageW(self.thread_id, 0x12, 0, 0)  # WM_QUIT
        self.thread.join(2)

    def run(self) -> None:
        hooks: list[int] = []
        events: list[int] = []
        timer = 0
        try:
            self.user = windows_ctypes.WinDLL("user32", use_last_error=True)
            kernel = windows_ctypes.WinDLL("kernel32", use_last_error=True)
            hook_type = windows_ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
            )
            event_type = windows_ctypes.WINFUNCTYPE(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                wintypes.LONG,
                wintypes.LONG,
                wintypes.DWORD,
                wintypes.DWORD,
            )
            self.user.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                hook_type,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            ]
            self.user.SetWindowsHookExW.restype = wintypes.HANDLE
            self.user.CallNextHookEx.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            ]
            self.user.CallNextHookEx.restype = ctypes.c_ssize_t
            self.user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
            self.user.SetWinEventHook.argtypes = [
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HMODULE,
                event_type,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            self.user.SetWinEventHook.restype = wintypes.HANDLE
            self.user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            self.user.PostThreadMessageW.argtypes = [
                wintypes.DWORD,
                wintypes.UINT,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            ]
            self.user.SetTimer.argtypes = [
                wintypes.HWND,
                ctypes.c_size_t,
                wintypes.UINT,
                wintypes.LPVOID,
            ]
            self.user.SetTimer.restype = ctypes.c_size_t
            self.user.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
            self.user.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            ]
            self.user.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            self.user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            self.user.DispatchMessageW.restype = ctypes.c_ssize_t
            kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = (
                [wintypes.LPCWSTR],
                wintypes.HMODULE,
            )

            def keyboard(code: int, message: int, pointer: int) -> int:
                if code == 0:
                    event = ctypes.cast(pointer, ctypes.POINTER(KeyboardEvent)).contents
                    self.counter.input(event.flags, event.extra, 0x10)
                    if event.flags & 0x10 and event.extra == self.counter.tag and self.own_keyboard:
                        self.own_keyboard(event.vk, event.scan, event.flags)
                return int(self.user.CallNextHookEx(None, code, message, pointer))

            def mouse(code: int, message: int, pointer: int) -> int:
                if code == 0:
                    event = ctypes.cast(pointer, ctypes.POINTER(MouseEvent)).contents
                    self.counter.mouse_events += 1
                    self.counter.mouse_marked += event.extra == self.counter.tag
                    self.counter.mouse_injected += bool(event.flags & 1)
                    self.counter.input(event.flags, event.extra, 1)
                    if event.flags & 1 and event.extra == self.counter.tag and self.own_mouse:
                        self.own_mouse(message, event.data)
                return int(self.user.CallNextHookEx(None, code, message, pointer))

            def changed(
                hook: int,
                event: int,
                handle: int,
                object_id: int,
                child_id: int,
                thread_id: int,
                event_time: int,
            ) -> None:
                self.counter.window_event(event, handle, object_id, child_id)

            # Keep callback objects strongly alive until hooks are unregistered.
            keyboard_hook, mouse_hook, event_hook = (
                hook_type(keyboard),
                hook_type(mouse),
                event_type(changed),
            )
            self.callbacks = (keyboard_hook, mouse_hook, event_hook)
            message = wintypes.MSG()
            self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # Create thread queue.
            self.thread_id = int(kernel.GetCurrentThreadId())
            for kind, callback in ((13, keyboard_hook), (14, mouse_hook)):
                handle = self.user.SetWindowsHookExW(
                    kind, callback, kernel.GetModuleHandleW(None), 0
                )
                if not handle:
                    raise windows_ctypes.WinError(windows_ctypes.get_last_error())
                hooks.append(handle)
            for low, high in ((3, 3), (0x8000, 0x8001)):
                handle = self.user.SetWinEventHook(low, high, None, event_hook, 0, 0, 0)
                if not handle:
                    raise windows_ctypes.WinError(windows_ctypes.get_last_error())
                events.append(handle)
            timer = self.user.SetTimer(None, 0, 50, None)
            if not timer:
                raise windows_ctypes.WinError(windows_ctypes.get_last_error())
            self.heartbeat = time.monotonic()
            self.ready.set()
            while not self.stopped.is_set():
                result = self.user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    if result < 0:
                        raise windows_ctypes.WinError(windows_ctypes.get_last_error())
                    break
                self.heartbeat = time.monotonic()
                if message.message == 0x8001:
                    self.fence.set()
                elif message.message != 0x113:
                    self.user.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:
            self.error = exc
        finally:
            self.ready.set()
            for handle in hooks:
                self.user.UnhookWindowsHookEx(handle)
            for handle in events:
                self.user.UnhookWinEvent(handle)
            if timer:
                self.user.KillTimer(None, timer)
