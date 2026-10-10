"""Physical-pixel Win32 inspection and guarded input on the Broker's own desktop."""

import ctypes
import hashlib
import json
import os
import secrets
import time
from ctypes import wintypes
from typing import Any

from racp_domain.models import RACPError

from racp_agent.broker.automation import Automation
from racp_agent.broker.guard_client import GuardClient
from racp_agent.broker.guard_config import GuardConfig
from racp_agent.broker.identity import process_identity
from racp_agent.broker.input_lease import SessionInputLease
from racp_agent.broker.input_watch import InputWatch

windows_ctypes: Any = ctypes


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class KeyInput(ctypes.Structure):
    _fields_ = [
        ("vk", wintypes.WORD),
        ("scan", wintypes.WORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [("message", wintypes.DWORD), ("low", wintypes.WORD), ("high", wintypes.WORD)]


class InputUnion(ctypes.Union):
    _fields_ = [("mouse", MouseInput), ("key", KeyInput), ("hardware", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]


class WindowsDesktop:
    def __init__(self, session_id: int, *, require_guard: bool = False) -> None:
        self.identity = process_identity(os.getpid())
        if self.identity.session != session_id or session_id == 0:
            raise RACPError(
                "SESSION_UNAVAILABLE",
                "Broker requires its interactive user session",
                layer="broker",
            )
        self.session_id = session_id
        self.require_guard = require_guard
        self.guard: GuardClient | None = None
        import win32security

        name, domain, _ = win32security.LookupAccountSid(
            None, win32security.ConvertStringSidToSid(self.identity.sid)
        )
        self.user_name = f"{domain}\\{name}"
        self.user = windows_ctypes.WinDLL("user32", use_last_error=True)
        self.user.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        self.user.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
        self.user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.user.OpenInputDesktop.restype = wintypes.HANDLE
        self.user.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user.GetProcessWindowStation.restype = wintypes.HANDLE
        self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user.SendInput.restype = wintypes.UINT
        self.user.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user.GetDpiForWindow.restype = wintypes.UINT
        self.dpi()
        # Some Windows mouse paths round-trip only the low 32 bits of dwExtraInfo.
        # A positive 31-bit marker also avoids sign-extension mismatches in that path.
        self.tag = secrets.randbits(31) or 1
        self.salt = secrets.token_hex(16)
        self.targets: dict[str, int] = {}
        self.pressed: list[Input] = []
        self.input_lease = SessionInputLease(session_id, self.identity.sid)
        try:
            self.watch = InputWatch(self.tag)
        except BaseException:
            self.input_lease.close()
            raise
        self.automation: Automation | None = None
        try:
            self.automation = Automation(self.identity)
        except (OSError, ImportError, RACPError):
            pass  # Report this optional structured backend separately from Win32 availability.

    def close(self) -> None:
        try:
            try:
                self.release_inputs()
            finally:
                if self.automation is not None:
                    self.automation.close()
                    self.automation = None
                if self.guard is not None:
                    self.guard.close()
                    self.guard = None
        finally:
            try:
                self.input_lease.close()
            finally:
                self.watch.close()

    def acquire_lease(self) -> None:
        self.watch.sync()
        self.input_lease.acquire()

    def attach_guard(self, raw: dict[str, Any]) -> dict[str, Any]:
        config = GuardConfig.model_validate(raw)
        if self.guard is not None:
            raise RACPError("RESOURCE_BUSY", "input guardian already attached", layer="broker")
        guard = GuardClient(config)
        self.guard = guard
        self.tag = config.marker
        self.watch.counter.tag = self.tag
        return {"attached": True, "guardian_pid": config.guardian_pid}

    def release_lease(self) -> None:
        self.input_lease.release()

    def clipboard(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        from racp_agent.broker.clipboard import (
            ClipboardBusy,
            ClipboardChanged,
            InvalidClipboardData,
            WindowsClipboard,
        )

        def guard() -> None:
            state = self._status()
            if not state["available"]:
                raise RACPError(
                    state["error_code"], state["reason"], layer="broker",
                    execution_state="not_started",
                )

        try:
            with WindowsClipboard() as clipboard:
                if operation == "clipboard.state":
                    return clipboard.state()
                if operation == "clipboard.read":
                    return clipboard.read(payload["max_bytes"])
                return clipboard.write(payload["text"], payload["expected_sequence"], guard=guard)
        except ClipboardChanged:
            raise RACPError(
                "PRECONDITION_FAILED", "Clipboard changed; read its current sequence first",
                layer="broker", reason="CLIPBOARD_CHANGED", execution_state="not_started",
            ) from None
        except ClipboardBusy:
            raise RACPError(
                "RESOURCE_BUSY", "Clipboard is in use or access is denied", layer="broker",
                execution_state="not_started",
            ) from None
        except InvalidClipboardData:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "Clipboard Unicode text is unreadable", layer="broker"
            ) from None

    def guard_info(self) -> dict[str, Any]:
        return {"guardian": self.guard.config.model_dump() if self.guard is not None else None}

    def dpi(self) -> None:
        if not self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "broker_pid": self.identity.pid,
            "broker_created": self.identity.created,
            "user_sid": self.identity.sid,
            "user_name": self.user_name,
            "integrity": self.identity.integrity,
            "user_input_detection_complete": False,
            "input_detection_backend": "low_level_hooks_with_self_input_tag",
            "input_hook_healthy": self.watch.healthy(),
            "ui_automation_available": self.automation is not None,
            "input_guardian_available": self.guard is not None
            and self.guard.peer.returncode is None,
            **self._status(),
        }

    def _status(self) -> dict[str, Any]:
        self.dpi()
        if not self.watch.healthy():
            return {
                "available": False,
                "error_code": "SESSION_UNAVAILABLE",
                "reason": "desktop hook pump unavailable",
            }
        from racp_agent.broker.session_state import query, same_logon

        session = query(self.session_id)
        station = self.user.GetProcessWindowStation()
        station_name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
        if (
            not station
            or not self.user.GetUserObjectInformationW(
                station, 2, station_name, ctypes.sizeof(station_name), ctypes.byref(needed)
            )
            or station_name.value != "WinSta0"
        ):
            return {
                "available": False,
                "error_code": "SESSION_UNAVAILABLE",
                "reason": "noninteractive window station",
            }
        if not same_logon(session, self.identity.sid):
            return {
                "available": False,
                "error_code": "SESSION_UNAVAILABLE",
                "reason": "session logged off or changed user",
            }
        if session["state"] != "active":
            return {
                "available": False,
                "error_code": "SESSION_UNAVAILABLE",
                "reason": "session is disconnected or inactive",
            }
        if session["locked"] is True:
            return {
                "available": False,
                "error_code": "SESSION_LOCKED",
                "reason": "session is locked",
                "locked": True,
            }
        desktop = self.user.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS only.
        if not desktop:
            return {
                "available": False,
                "error_code": "SESSION_UNAVAILABLE",
                "reason": "UAC secure desktop"
                if session["locked"] is False and windows_ctypes.get_last_error() == 5
                else "input desktop unavailable; lock state unknown",
            }
        try:
            name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
            if not self.user.GetUserObjectInformationW(
                desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
            ):
                raise windows_ctypes.WinError(windows_ctypes.get_last_error())
            if name.value != "Default":
                return {
                    "available": False,
                    "error_code": "SESSION_UNAVAILABLE",
                    "reason": "UAC secure desktop"
                    if session["locked"] is False
                    else "secure input desktop; lock state unknown",
                }
        finally:
            self.user.CloseDesktop(desktop)
        return {
            "available": True,
            "session_id": self.session_id,
            "user_sid": self.identity.sid,
            "integrity": self.identity.integrity,
            "user_input_detection_complete": False,
        }

    def layout(self) -> dict[str, Any]:
        import win32api

        self.dpi()
        monitors = []
        dpi_api = windows_ctypes.WinDLL("shcore", use_last_error=True).GetDpiForMonitor
        dpi_api.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        for handle, _, rectangle in win32api.EnumDisplayMonitors():
            info = win32api.GetMonitorInfo(handle)
            xdpi, ydpi = wintypes.UINT(), wintypes.UINT()
            if dpi_api(int(handle), 0, ctypes.byref(xdpi), ctypes.byref(ydpi)):
                raise RACPError("SESSION_UNAVAILABLE", "monitor DPI unavailable", layer="broker")
            left, top, right, bottom = rectangle
            monitors.append(
                {
                    "device": info["Device"],
                    "origin": {"x": left, "y": top},
                    "width": right - left,
                    "height": bottom - top,
                    "scale_x": xdpi.value / 96,
                    "scale_y": ydpi.value / 96,
                }
            )
        if not monitors or len(monitors) > 32:
            raise RACPError("SESSION_UNAVAILABLE", "unsupported monitor layout", layer="broker")
        monitors.sort(key=lambda m: str(m["device"]))
        revision = hashlib.sha256(
            (self.salt + json.dumps(monitors, sort_keys=True)).encode()
        ).hexdigest()
        for index, monitor in enumerate(monitors):
            monitor["monitor_id"] = "monitor_" + revision[:16] + "_" + str(index)
        return {
            "session_id": self.session_id,
            "layout_revision": revision,
            "monitors": monitors,
            "virtual_origin": {
                "x": win32api.GetSystemMetrics(76),
                "y": win32api.GetSystemMetrics(77),
            },
            "width": win32api.GetSystemMetrics(78),
            "height": win32api.GetSystemMetrics(79),
        }

    def window(self, handle: int) -> dict[str, Any]:
        import win32gui
        import win32process

        self.dpi()
        if not win32gui.IsWindow(handle) or not win32gui.IsWindowVisible(handle):
            raise RACPError("STALE_OBSERVATION", "window disappeared", layer="broker")
        _, pid = win32process.GetWindowThreadProcessId(handle)
        identity = process_identity(pid)
        if identity.session != self.session_id:
            raise RACPError(
                "PERMISSION_DENIED", "window belongs to another session", layer="broker"
            )
        key = (
            "window_"
            + hashlib.sha256(
                f"{self.salt}:{handle}:{pid}:{identity.created}:"
                f"{self.watch.counter.generation(handle)}".encode()
            ).hexdigest()[:32]
        )
        self.targets[key] = handle
        return {
            "window_id": key,
            "pid": pid,
            "create_time": identity.created,
            "bounds": list(win32gui.GetWindowRect(handle)),
            "integrity": identity.integrity,
            "title": win32gui.GetWindowText(handle)[:256],
            "class_name": win32gui.GetClassName(handle)[:64],
            "dpi": int(self.user.GetDpiForWindow(handle)),
        }

    def windows(self, limit: int) -> list[dict[str, Any]]:
        import win32gui

        self.targets.clear()
        windows: list[dict[str, Any]] = []

        def collect(handle: int, _: Any) -> None:
            if len(windows) >= limit or not win32gui.IsWindowVisible(handle):
                return
            try:
                windows.append(self.window(handle))
            except Exception:
                # Inaccessible/elevated/exited windows are not input targets.
                pass

        win32gui.EnumWindows(collect, None)
        return windows

    def foreground(self) -> str | None:
        import win32gui

        handle = win32gui.GetForegroundWindow()
        try:
            return str(self.window(handle)["window_id"]) if handle else None
        except Exception:
            return None

    def validate_window(self, window: dict[str, Any]) -> bool:
        handle = self.targets.get(window["window_id"])
        if handle is None:
            return False
        try:
            current = self.window(handle)
        except Exception:
            return False
        if current["integrity"] > self.identity.integrity:
            raise RACPError(
                "INTEGRITY_LEVEL_MISMATCH", "target window has higher integrity", layer="broker"
            )
        return all(
            current[key] == window[key]
            for key in ("window_id", "pid", "create_time", "bounds", "dpi")
        )

    def input_tick(self) -> int:
        self.watch.sync()
        return self.watch.counter.sequence

    def foreground_tick(self) -> int:
        return self.watch.counter.foreground_sequence

    def send(self, inputs: list[Input]) -> None:
        if self.require_guard and self.guard is None:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "independent input guardian unavailable", layer="broker"
            )
        guard_before = self.guard.arm() if self.guard is not None else 0
        self.watch.sync()
        before = self.watch.counter.own_sequence
        mouse_before = self.watch.counter.mouse_events
        marked_before = self.watch.counter.mouse_marked
        injected_before = self.watch.counter.mouse_injected
        buffer = (Input * len(inputs))(*inputs)
        if self.user.SendInput(len(inputs), buffer, ctypes.sizeof(Input)) != len(inputs):
            raise RACPError(
                "INPUT_DISPATCH_FAILED",
                "OS rejected input dispatch",
                layer="broker",
                execution_state="unknown",
            )
        self.watch.sync()
        # A zero-distance move can be suppressed before the hook. Require an echo for
        # keyboard, button and wheel input; movement alone is not a hook liveness probe.
        required = sum(
            event.type == 1 or event.type == 0 and bool(event.value.mouse.flags & ~0xE001)
            for event in inputs
        )
        observed = self.watch.counter.own_sequence - before
        echo_deadline = time.monotonic() + 0.1
        while observed < required and time.monotonic() < echo_deadline:
            # Mouse delivery can trail SendInput and the first posted pump fence.
            time.sleep(0.005)
            self.watch.sync()
            observed = self.watch.counter.own_sequence - before
        if observed < required:
            raise RACPError(
                "INPUT_DISPATCH_FAILED",
                f"input hook dispatch echo missing (expected={required}, observed={observed}, "
                f"mouse={self.watch.counter.mouse_events - mouse_before}, "
                f"marked={self.watch.counter.mouse_marked - marked_before}, "
                f"injected={self.watch.counter.mouse_injected - injected_before})",
                layer="broker",
                execution_state="unknown",
                expected_events=required,
                observed_events=observed,
            )
        if self.guard is not None:
            self.guard.observed(guard_before, required)

    def key(self, vk: int = 0, scan: int = 0, flags: int = 0) -> Input:
        return Input(1, InputUnion(key=KeyInput(vk, scan, flags, 0, self.tag)))

    def mouse(self, flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> Input:
        return Input(0, InputUnion(mouse=MouseInput(dx, dy, data & 0xFFFFFFFF, flags, 0, self.tag)))

    def release_inputs(self) -> None:
        pending = list(reversed(self.pressed))
        if pending:
            # Hook failure must never suppress the attempt to release our keys/buttons.
            buffer = (Input * len(pending))(*pending)
            count = int(self.user.SendInput(len(pending), buffer, ctypes.sizeof(Input)))
            self.pressed = list(reversed(pending[count:]))
            if count != len(pending):
                raise RACPError(
                    "INPUT_DISPATCH_FAILED",
                    "OS rejected owned input release",
                    layer="broker",
                    execution_state="unknown",
                    cleanup_status="unverified",
                )
        if self.guard is not None:
            self.guard.idle()

    def point(self, payload: dict[str, Any], x: int, y: int) -> Input:
        import win32gui

        layout = self.layout()
        origin = layout["virtual_origin"]
        if not (
            origin["x"] <= x < origin["x"] + layout["width"]
            and origin["y"] <= y < origin["y"] + layout["height"]
        ):
            raise RACPError("INVALID_ARGUMENT", "point outside virtual desktop", layer="broker")
        target = win32gui.GetAncestor(win32gui.WindowFromPoint((x, y)), 2)  # GA_ROOT
        if target != self.targets.get(payload["expected_window_id"]):
            raise RACPError(
                "STALE_OBSERVATION", "point is covered by another window", layer="broker"
            )
        dx = round((x - origin["x"]) * 65535 / max(1, layout["width"] - 1))
        dy = round((y - origin["y"]) * 65535 / max(1, layout["height"] - 1))
        return self.mouse(0xC001, dx=dx, dy=dy)  # MOVE | ABSOLUTE | VIRTUALDESK

    def action(self, operation: str, payload: dict[str, Any], guard: Any) -> dict[str, Any]:
        import win32gui

        guard()
        if operation in {"desktop.invoke", "desktop.set_value"}:
            if self.automation is None:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE", "UI Automation unavailable", layer="broker"
                )
            return self.automation.action(operation, payload, guard)
        if operation == "desktop.activate":
            target = self.targets[payload["expected_window_id"]]
            try:
                if win32gui.GetForegroundWindow() != target:
                    win32gui.SetForegroundWindow(target)
            except Exception:
                pass  # Win32 may reject activation; actual foreground below decides the result.
            if win32gui.GetForegroundWindow() != target:
                raise RACPError(
                    "FOCUS_MISMATCH", "OS refused foreground activation", layer="broker"
                )
            return {"activated": True}
        if self.require_guard and self.guard is None:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "independent input guardian unavailable", layer="broker"
            )
        if self.guard is not None:
            self.guard.before_send()
        # Reject pre-existing physical modifiers/buttons; release only our own input.
        if any(self.user.GetAsyncKeyState(code) & 0x8000 for code in (1, 2, 4, 16, 17, 18, 91, 92)):
            raise RACPError(
                "RESOURCE_BUSY", "user modifier or mouse button is held", layer="broker"
            )
        if operation == "desktop.drag":
            # No transport wait spans a held button; each short step checks interruption fences.
            start = self.point(payload, payload["x"], payload["y"])
            self.point(payload, payload["end_x"], payload["end_y"])
            guard()
            self.pressed.append(self.mouse(4))
            self.send([start, self.mouse(2)])
            steps = max(1, (payload["duration_ms"] + 9) // 10)
            deadline = time.monotonic()
            for index in range(1, steps + 1):
                deadline += payload["duration_ms"] / 1000 / steps
                time.sleep(max(0, deadline - time.monotonic()))
                guard()
                x = round(payload["x"] + (payload["end_x"] - payload["x"]) * index / steps)
                y = round(payload["y"] + (payload["end_y"] - payload["y"]) * index / steps)
                self.send([self.point(payload, x, y)])
            guard()
            self.send([self.mouse(4)])
            self.pressed.clear()
            return {"dispatched": True}
        inputs: list[Input] = []
        if operation in {"desktop.move", "desktop.click", "desktop.scroll"}:
            inputs.append(self.point(payload, payload["x"], payload["y"]))
        if operation == "desktop.click":
            down, up = {"left": (2, 4), "right": (8, 16), "middle": (32, 64)}[payload["button"]]
            self.pressed.append(self.mouse(up))
            for _ in range(payload["click_count"]):
                inputs.extend([self.mouse(down), self.mouse(up)])
        elif operation == "desktop.scroll":
            inputs.append(self.mouse(0x800, payload["delta"]))
        elif operation == "desktop.type":
            units = payload["text"].encode("utf-16-le", errors="strict")
            for index in range(0, len(units), 2):
                code = int.from_bytes(units[index : index + 2], "little")
                inputs.extend([self.key(scan=code, flags=4), self.key(scan=code, flags=6)])
                self.pressed.append(self.key(scan=code, flags=6))
        elif operation == "desktop.key":
            keys = {
                "CTRL": 17,
                "ALT": 18,
                "SHIFT": 16,
                "ENTER": 13,
                "TAB": 9,
                "ESC": 27,
                "BACKSPACE": 8,
                "DELETE": 46,
                "HOME": 36,
                "END": 35,
                "LEFT": 37,
                "RIGHT": 39,
                "UP": 38,
                "DOWN": 40,
                "SPACE": 32,
            }
            for key in payload["keys"]:
                code = keys.get(key, ord(key) if len(key) == 1 else 0)
                extended = 1 if code in {46, 36, 35, 37, 39, 38, 40} else 0
                self.pressed.append(self.key(vk=code, flags=2 | extended))
                inputs.append(self.key(vk=code, flags=extended))
            inputs.extend(reversed(self.pressed))
        guard()
        # One SendInput batch is inserted serially without interleaving other input.
        # There is no pressed-key/button API spanning requests or transport waits.
        self.send(inputs)
        self.pressed.clear()
        return {"dispatched": True}

    def inspect(self, payload: dict[str, Any], observation: str) -> dict[str, Any]:
        if self.automation is None:
            raise RACPError("CAPABILITY_UNAVAILABLE", "UI Automation unavailable", layer="broker")
        return self.automation.inspect(
            self.targets[payload["window_id"]], payload["window_id"], observation, payload["limit"]
        )

    def clear_observations(self) -> None:
        if self.automation is not None:
            self.automation.clear()

    def capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        from racp_agent.broker.capture import dib, png, preview

        self.dpi()
        layout = self.layout()
        origin, width, height = layout["virtual_origin"], layout["width"], layout["height"]
        left, top = origin["x"], origin["y"]
        window: dict[str, Any] | None = None
        if payload["window_id"]:
            handle = self.targets.get(payload["window_id"])
            if handle is None:
                raise RACPError(
                    "STALE_OBSERVATION", "capture window is unavailable", layer="broker"
                )
            window = self.window(handle)
            if window["integrity"] > self.identity.integrity:
                raise RACPError(
                    "INTEGRITY_LEVEL_MISMATCH",
                    "capture target has higher integrity",
                    layer="broker",
                )
            left, top, right, bottom = window["bounds"]
            width, height = right - left, bottom - top
        elif payload["monitor_id"]:
            monitor = next(
                (m for m in layout["monitors"] if m["monitor_id"] == payload["monitor_id"]), None
            )
            if monitor is None:
                raise RACPError(
                    "STALE_OBSERVATION", "capture monitor revision changed", layer="broker"
                )
            left, top = monitor["origin"]["x"], monitor["origin"]["y"]
            width, height = monitor["width"], monitor["height"]
        if not self.status()["available"]:
            raise RACPError("SESSION_UNAVAILABLE", "session changed before capture", layer="broker")
        self.user.GetDC.argtypes, self.user.GetDC.restype = [wintypes.HWND], wintypes.HDC
        self.user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        dc = self.user.GetDC(None)
        if not dc:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        try:
            pixels = dib(dc, left, top, width, height)
        finally:
            self.user.ReleaseDC(None, dc)
        # Capture is the visible screen rectangle, including occluding windows.
        if window is not None and not self.validate_window(window):
            raise RACPError("STALE_OBSERVATION", "window moved during capture", layer="broker")
        status = self.status()
        if not status["available"]:
            raise RACPError(status["error_code"], status["reason"], layer="broker")
        result = {
            "_png": png(width, height, pixels),
            "width": width,
            "height": height,
            "crop_origin": {"x": left, "y": top},
            "format": "png",
            "window_id": payload["window_id"],
            "monitor_id": payload["monitor_id"],
            "capture_scope": "visible_rectangle",
        }
        if payload["preview"]:
            encoded, metadata = preview(width, height, pixels)
            result["_preview_png"] = encoded
            result["preview"] = {**metadata, "crop_origin": {"x": left, "y": top}, "format": "png"}
        return result
