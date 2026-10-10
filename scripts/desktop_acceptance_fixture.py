"""Owned native Windows GUI for W02/W03; records results without injecting input.

Run with the server development Python environment and a new result path inside the allowed
workspace. Bind its PID/creation time/session/HWND to a fresh desktop.windows result
before acquiring a lease. Agent boot/epoch and observation IDs come from MCP, not
this fixture. Close normally through its title bar or wait for the timeout.
"""

import argparse
import ctypes
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from racp_domain.version import VERSION


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created: float
    session: int


def process_identity(pid: int) -> ProcessIdentity:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel.ProcessIdToSessionId
    query.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    session = wintypes.DWORD()
    process = psutil.Process(pid)
    created = process.create_time()
    if not query(pid, ctypes.byref(session)) or process.create_time() != created:
        raise OSError("Unable to pin the fixture process session")
    return ProcessIdentity(pid, created, session.value)


class AcceptanceWindow:
    def __init__(self, run_id: str, output: Path, timeout: int) -> None:
        import win32api
        import win32con
        import win32gui

        self.api, self.con, self.gui = win32api, win32con, win32gui
        self.identity = process_identity(os.getpid())
        self.boot_time = psutil.boot_time()
        if self.identity.session == 0:
            raise RuntimeError("An unlocked interactive Windows session is required")
        self.run_id, self.output, self.timeout = run_id, output, timeout
        self.title = "RACP acceptance " + run_id
        self.instance = win32api.GetModuleHandle(None)
        self.handle = self.edit = self.button = self.canvas = 0
        self.clicks = self.double_clicks = self.right_clicks = 0
        self.scroll_x = self.scroll_y = 0
        self.drag_position = [40, 60]
        self.dragging = False
        self.drag_events = 0
        self.key_events: list[int] = []
        self.started = time.monotonic()
        self.closed = False
        self.close_reason: str | None = None
        self.revision = 0
        self.previous: dict[str, Any] = {}
        self.callbacks: list[Any] = []
        self.class_names: list[str] = []

    def register(self, suffix: str, callback: Any) -> str:
        cls = self.gui.WNDCLASS()
        cls.hInstance = self.instance
        cls.lpszClassName = self.title + suffix
        cls.hbrBackground = self.con.COLOR_WINDOW + 1
        cls.style = self.con.CS_DBLCLKS
        cls.lpfnWndProc = callback
        self.gui.RegisterClass(cls)
        self.callbacks.append(callback)
        self.class_names.append(cls.lpszClassName)
        return str(cls.lpszClassName)

    def record(self) -> None:
        gui = self.gui
        live = bool(self.handle and gui.IsWindow(self.handle))
        value = {
            "schema_version": 1,
            "run_id": self.run_id,
            "candidate_version": VERSION,
            "title": self.title,
            "pid": self.identity.pid,
            "process_created_at": self.identity.created,
            "session_id": self.identity.session,
            "os_boot_time": self.boot_time,
            "hwnd": self.handle,
            "window_bounds": list(gui.GetWindowRect(self.handle)) if live else None,
            "control_bounds": {
                name: list(gui.GetWindowRect(handle))
                for name, handle in [
                    ("text", self.edit),
                    ("button", self.button),
                    ("scroll_drag", self.canvas),
                ]
                if live and handle and gui.IsWindow(handle)
            },
            "control_client_origins": {
                name: list(gui.ClientToScreen(handle, (0, 0)))
                for name, handle in [
                    ("text", self.edit),
                    ("button", self.button),
                    ("scroll_drag", self.canvas),
                ]
                if live and handle and gui.IsWindow(handle)
            },
            "text": gui.GetWindowText(self.edit)
            if live and self.edit
            else self.previous.get("text", ""),
            "clicks": self.clicks,
            "double_clicks": self.double_clicks,
            "right_clicks": self.right_clicks,
            "scroll_offset": [self.scroll_x, self.scroll_y],
            "drag_position": list(self.drag_position),
            "drag_events": self.drag_events,
            "dragging": self.dragging,
            "key_events": list(self.key_events),
            "focus_hwnd": gui.GetFocus() if live else 0,
            "foreground_is_fixture": gui.GetForegroundWindow() == self.handle if live else False,
            "closed": self.closed,
            "close_reason": self.close_reason,
        }
        if value == self.previous:
            return
        self.revision += 1
        temporary = self.output.with_suffix(self.output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {**value, "revision": self.revision, "recorded_at": datetime.now(UTC).isoformat()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        publish_deadline = time.monotonic() + 0.5
        while True:
            try:
                temporary.replace(self.output)
                break
            except PermissionError as error:
                # Windows readers may briefly deny delete sharing during observation.
                if error.winerror not in {5, 32, 33} or time.monotonic() >= publish_deadline:
                    raise
                time.sleep(0.01)
        self.previous = value

    def window_proc(self, handle: int, message: int, wparam: int, lparam: int) -> int:
        con, gui = self.con, self.gui
        if message == con.WM_COMMAND and wparam & 0xFFFF == 102:
            notification = (wparam >> 16) & 0xFFFF
            if notification == con.BN_CLICKED:
                self.clicks += 1
            elif notification == con.BN_DOUBLECLICKED:
                self.double_clicks += 1
            return 0
        if message == con.WM_APP:
            if time.monotonic() - self.started >= self.timeout:
                self.close_reason = "timeout"
                gui.PostMessage(handle, con.WM_CLOSE, 0, 0)
            self.record()
            return 0
        if message == con.WM_CLOSE:
            self.close_reason = self.close_reason or "normal_close"
            if self.dragging:
                gui.ReleaseCapture()
                self.dragging = False
            gui.DestroyWindow(handle)
            return 0
        if message == con.WM_DESTROY:
            self.closed = True
            gui.PostQuitMessage(0)
            return 0
        return int(gui.DefWindowProc(handle, message, wparam, lparam))

    def canvas_proc(self, handle: int, message: int, wparam: int, lparam: int) -> int:
        con, gui = self.con, self.gui
        if message == con.WM_KEYDOWN and self.key_event(handle, wparam):
            return 0
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        if message in (con.WM_MOUSEWHEEL, 0x020E):  # WM_MOUSEHWHEEL
            delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            if message == con.WM_MOUSEWHEEL:
                self.scroll_y -= delta
            else:
                self.scroll_x += delta
            gui.InvalidateRect(handle, None, True)
            return 0
        if message == con.WM_LBUTTONDOWN:
            px, py = self.drag_position
            gui.SetFocus(handle)
            if px <= x <= px + 40 and py <= y <= py + 40:
                self.dragging = True
                gui.SetCapture(handle)
            return 0
        if message == con.WM_MOUSEMOVE and self.dragging:
            self.drag_position = [max(0, min(x - 20, 520)), max(30, min(y - 20, 120))]
            gui.InvalidateRect(handle, None, True)
            return 0
        if message == con.WM_LBUTTONUP and self.dragging:
            self.drag_events += 1
            self.dragging = False
            gui.ReleaseCapture()
            return 0
        if message == con.WM_CAPTURECHANGED:
            self.dragging = False
            return 0
        if message == con.WM_PAINT:
            dc, paint = gui.BeginPaint(handle)
            try:
                gui.DrawText(
                    dc,
                    f"Scroll offset: {self.scroll_x}, {self.scroll_y}",
                    -1,
                    (10, 5, 550, 30),
                    con.DT_LEFT,
                )
                px, py = self.drag_position
                gui.Rectangle(dc, px, py, px + 40, py + 40)
            finally:
                gui.EndPaint(handle, paint)
            return 0
        return int(gui.DefWindowProc(handle, message, wparam, lparam))

    def key_event(self, handle: int, key: int) -> bool:
        self.key_events = (self.key_events + [key])[-32:]
        if key == self.con.VK_TAB:
            controls = [self.edit, self.button, self.canvas]
            direction = -1 if self.api.GetKeyState(self.con.VK_SHIFT) < 0 else 1
            self.gui.SetFocus(controls[(controls.index(handle) + direction) % 3])
            return True
        return False

    def run(self) -> None:
        con, gui = self.con, self.gui
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetProcessDPIAware()
        name = self.register("-main", self.window_proc)
        canvas_class = self.register("-canvas", self.canvas_proc)
        stopping = threading.Event()
        pump: threading.Thread | None = None
        try:
            self.handle = gui.CreateWindow(
                name,
                self.title,
                con.WS_OVERLAPPEDWINDOW,
                100,
                100,
                640,
                480,
                0,
                0,
                self.instance,
                None,
            )
            self.edit = gui.CreateWindow(
                "EDIT",
                "",
                con.WS_CHILD
                | con.WS_VISIBLE
                | con.WS_BORDER
                | con.WS_TABSTOP
                | con.ES_MULTILINE
                | con.ES_AUTOVSCROLL
                | con.WS_VSCROLL,
                20,
                20,
                560,
                120,
                self.handle,
                101,
                self.instance,
                None,
            )
            self.button = gui.CreateWindow(
                "BUTTON",
                "Fixture Invoke",
                con.WS_CHILD | con.WS_VISIBLE | con.WS_TABSTOP | con.BS_NOTIFY,
                20,
                155,
                180,
                40,
                self.handle,
                102,
                self.instance,
                None,
            )
            self.canvas = gui.CreateWindow(
                canvas_class,
                "Scroll and drag",
                con.WS_CHILD | con.WS_VISIBLE | con.WS_BORDER | con.WS_TABSTOP,
                20,
                210,
                560,
                170,
                self.handle,
                103,
                self.instance,
                None,
            )
            # Only subclass native controls. Subclassing our Python canvas with the
            # same pywin32 dispatcher would recursively call its replacement proc.
            for control in [self.edit, self.button]:
                previous: list[int] = []

                def callback(
                    hwnd: int, msg: int, wp: int, lp: int, old: list[int] = previous
                ) -> int:
                    if msg == con.WM_KEYDOWN:
                        if self.key_event(hwnd, wp):
                            return 0
                    if hwnd == self.button and msg == con.WM_RBUTTONUP:
                        self.right_clicks += 1
                    return int(gui.CallWindowProc(old[0], hwnd, msg, wp, lp))

                self.callbacks.append(callback)
                # The pointer-sized return preserves the native procedure on x64.
                previous.append(gui.SetWindowLong(control, con.GWL_WNDPROC, callback))
            gui.ShowWindow(self.handle, con.SW_SHOWNOACTIVATE)
            self.record()

            def refresh() -> None:
                while not stopping.wait(0.1):
                    try:
                        gui.PostMessage(self.handle, con.WM_APP, 0, 0)
                    except gui.error:
                        return

            pump = threading.Thread(target=refresh, name="fixture-results", daemon=True)
            pump.start()
            gui.PumpMessages()
        finally:
            stopping.set()
            if pump is not None:
                pump.join(2)
            if self.handle and gui.IsWindow(self.handle):
                gui.DestroyWindow(self.handle)
            self.closed = True
            self.record()
            for cls in reversed(self.class_names):
                gui.UnregisterClass(cls, self.instance)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--timeout", type=int, default=900, help="Window lifetime in seconds (1–3600)"
    )
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id):
        parser.error("run-id must contain 1–80 ASCII letters, digits, underscores or hyphens")
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be between 1 and 3600 seconds")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the path exclusively; never overwrite an earlier run's evidence.
    with output.open("x", encoding="utf-8") as stream:
        stream.write("{}\n")
    AcceptanceWindow(args.run_id, output, args.timeout).run()


if __name__ == "__main__":
    main()
