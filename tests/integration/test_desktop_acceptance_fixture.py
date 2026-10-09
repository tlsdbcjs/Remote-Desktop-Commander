"""W02 fixture acceptance: independent native results and owned-process cleanup."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest
from racp_agent.broker.core import BrokerContext, BrokerCore
from racp_agent.broker.identity import process_identity
from racp_agent.broker.windows import WindowsDesktop

pytestmark = [
    pytest.mark.native_gui,
    pytest.mark.skipif(
        os.name != "nt" or os.environ.get("RACP_TEST_GUI") != "1",
        reason="opt-in owned Windows GUI fixture (RACP_TEST_GUI=1)",
    ),
]
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/desktop_acceptance_fixture.py"


def result_at(path: Path, predicate: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    value: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        if predicate(value):
            return value
        time.sleep(0.025)
    pytest.fail(f"Fixture result did not converge: {value}")


def test_result_publication_survives_a_transient_windows_reader(tmp_path: Path) -> None:
    import threading

    from scripts.desktop_acceptance_fixture import AcceptanceWindow

    path = tmp_path / "publication.json"
    window = AcceptanceWindow("reader-race", path, 5)
    window.record()
    reader = path.open("rb")  # The CRT reader does not share deletion on Windows.
    released = threading.Event()

    def release() -> None:
        released.wait(0.1)
        reader.close()

    thread = threading.Thread(target=release)
    thread.start()
    try:
        window.clicks = 1
        window.record()
        assert json.loads(path.read_text())["clicks"] == 1
    finally:
        released.set()
        thread.join(2)


@pytest.fixture
def standalone(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    import win32con
    import win32gui

    session = process_identity(os.getpid()).session
    if session == 0:
        pytest.skip("requires an interactive session")
    backend = WindowsDesktop(session)
    process = None
    old_foreground = win32gui.GetForegroundWindow()
    path = tmp_path / "owned-results.json"
    script = SCRIPT
    if getattr(request, "param", "native") == "zero-wndproc":
        # Reproduce the installed 121 runtime's observed pointer getter result.
        script = tmp_path / "zero-wndproc-fixture.py"
        script.write_text(
            "import runpy,win32gui,win32con\n"
            "original=win32gui.GetWindowLong\n"
            "win32gui.GetWindowLong=lambda h,i: 0 if i==win32con.GWL_WNDPROC else original(h,i)\n"
            f"runpy.run_path({str(SCRIPT)!r},run_name='__main__')\n",
            encoding="utf-8",
        )
    try:
        if not backend.status()["available"]:
            pytest.skip("interactive desktop unavailable")
        with (tmp_path / "fixture.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(script),
                    "--run-id",
                    "W02-native",
                    "--output",
                    str(path),
                    "--timeout",
                    "60",
                ],
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            data = result_at(path, lambda v: bool(v.get("hwnd")))
            owned_pids = {process.pid} | {
                p.pid for p in psutil.Process(process.pid).children(recursive=True)
            }
            assert data["pid"] in owned_pids
            identity = process_identity(data["pid"])
            assert data["process_created_at"] == identity.created
            assert data["session_id"] == session and not data["closed"]
            yield process, path, data, backend
    finally:
        backend.close()
        if process is not None:
            if process.poll() is None and path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                hwnd = data.get("hwnd", 0)
                if hwnd and win32gui.IsWindow(hwnd):
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                pytest.fail("Owned fixture did not close normally")
        if old_foreground and win32gui.IsWindow(old_foreground):
            try:
                win32gui.SetForegroundWindow(old_foreground)
            except Exception:
                pass


@pytest.mark.parametrize("standalone", ["native", "zero-wndproc"], indirect=True)
def test_standalone_preserves_native_control_message_procedure(standalone: Any) -> None:
    import ctypes

    import win32con
    import win32gui

    _, _, data, _ = standalone
    button = win32gui.GetDlgItem(data["hwnd"], 102)
    assert win32gui.IsWindowVisible(button)
    # WM_GETTEXT must reach the original native procedure after subclassing.
    # GetWindowLong(GWL_WNDPROC) returns zero on our 64-bit Windows runtime.
    text = ctypes.create_unicode_buffer(64)
    win32gui.SendMessageTimeout(
        button,
        win32con.WM_GETTEXT,
        len(text),
        ctypes.addressof(text),
        win32con.SMTO_ABORTIFHUNG,
        1000,
    )
    assert text.value == "Fixture Invoke"


def test_standalone_records_guarded_mouse_keyboard_scroll_drag_and_uia(standalone: Any) -> None:
    import win32gui

    process, path, data, backend = standalone
    handle = data["hwnd"]
    try:
        win32gui.SetForegroundWindow(handle)
    except Exception:
        pass
    if win32gui.GetForegroundWindow() != handle:
        pytest.skip("OS refused activation of the owned fixture")
    backend.watch.sync()
    core = BrokerCore(backend.session_id, backend)
    context = BrokerContext(owner_id="owner_w02", device_id="device_w02", operation_id="op_w02")
    lease = core.execute("desktop.lease_acquire", {"session_id": backend.session_id}, context)
    window = backend.window(handle)

    def action(operation: str, **extra: Any) -> None:
        nonlocal lease
        if core.lease is not None:
            core.execute(
                "desktop.lease_release",
                {"session_id": backend.session_id, "lease_id": lease["lease_id"]},
                context,
            )
        observation = core.execute("desktop.windows", {"session_id": backend.session_id}, context)
        lease = core.execute("desktop.lease_acquire", {"session_id": backend.session_id}, context)
        target = next(w for w in observation["windows"] if w["window_id"] == window["window_id"])
        assert target["pid"] == data["pid"] and target["create_time"] == data["process_created_at"]
        core.execute(
            operation,
            {
                "session_id": backend.session_id,
                "lease_id": lease["lease_id"],
                "observation_id": observation["observation_id"],
                "layout_revision": observation["layout_revision"],
                "expected_window_id": window["window_id"],
                **extra,
            },
            context,
        )

    def point(control: str, dx: int | None = None, dy: int | None = None) -> dict[str, int]:
        left, top, right, bottom = data["control_bounds"][control]
        client_x, client_y = data["control_client_origins"][control]
        return {
            "x": client_x + dx if dx is not None else (left + right) // 2,
            "y": client_y + dy if dy is not None else (top + bottom) // 2,
        }

    try:
        action("desktop.click", **point("button"), button="left", click_count=1)
        result_at(path, lambda v: v.get("clicks") == 1)
        action("desktop.click", **point("button"), button="right", click_count=1)
        result_at(path, lambda v: v.get("right_clicks") == 1)
        action("desktop.click", **point("button"), button="left", click_count=2)
        result_at(path, lambda v: v.get("double_clicks") == 1)
        action("desktop.click", **point("text"), button="left", click_count=1)
        action("desktop.type", text="RACP 한글 😀")
        result_at(path, lambda v: v.get("text") == "RACP 한글 😀")
        action("desktop.key", keys=["CTRL", "A"])
        action("desktop.key", keys=["BACKSPACE"])
        result_at(path, lambda v: v.get("text") == "")
        action("desktop.key", keys=["TAB"])
        result_at(path, lambda v: 9 in v.get("key_events", []))
        action("desktop.click", **point("scroll_drag", 200, 100), button="left", click_count=1)
        action("desktop.scroll", **point("scroll_drag", 200, 100), delta=-120)
        result_at(path, lambda v: v.get("scroll_offset") == [0, 120])
        action("desktop.scroll", **point("scroll_drag", 200, 100), delta=120)
        result_at(path, lambda v: v.get("scroll_offset") == [0, 0])
        start = point("scroll_drag", 60, 80)
        action(
            "desktop.drag", **start, end_x=start["x"] + 100, end_y=start["y"] + 20, duration_ms=200
        )
        dragged = result_at(path, lambda v: v.get("drag_events") == 1)
        assert dragged["drag_position"] == [140, 80] and not dragged["dragging"]
        for operation, automation_id, extra in [
            ("desktop.set_value", "101", {"value": "UIA 한글\r\nsecond line"}),
            ("desktop.invoke", "102", {}),
        ]:
            observed = core.execute(
                "desktop.inspect",
                {
                    "session_id": backend.session_id,
                    "window_id": window["window_id"],
                },
                context,
            )
            element = next(e for e in observed["elements"] if e["automation_id"] == automation_id)
            core.execute(
                operation,
                {
                    "session_id": backend.session_id,
                    "lease_id": lease["lease_id"],
                    "expected_window_id": window["window_id"],
                    "observation_id": observed["observation_id"],
                    "layout_revision": observed["layout_revision"],
                    "element_ref": element["element_ref"],
                    **extra,
                },
                context,
            )
        result_at(
            path, lambda v: v.get("text") == "UIA 한글\r\nsecond line" and v.get("clicks") == 3
        )
        assert not backend.pressed
        assert not any(backend.user.GetAsyncKeyState(k) & 0x8000 for k in (1, 2, 16, 17, 18))
    finally:
        core.abort()


def test_standalone_timeout_preserves_results_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "timeout.json"
    command = [
        sys.executable,
        str(SCRIPT),
        "--run-id",
        "W02-timeout",
        "--output",
        str(path),
        "--timeout",
        "1",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["closed"] and value["close_reason"] == "timeout"
    assert value["text"] == "" and value["clicks"] == 0 and not value["dragging"]
    import win32gui

    assert not win32gui.IsWindow(value["hwnd"])
    original = path.read_bytes()
    repeated = subprocess.run(command, capture_output=True, timeout=10)
    assert repeated.returncode != 0 and path.read_bytes() == original
