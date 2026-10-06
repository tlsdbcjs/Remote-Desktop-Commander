"""Opt-in acceptance on temporary windows owned by this test; no other app is an input target."""

import asyncio
import ctypes
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from racp_agent.broker.core import BrokerContext, BrokerCore
from racp_agent.broker.identity import process_identity
from racp_agent.broker.windows import Input, WindowsDesktop
from racp_domain.models import RACPError

pytestmark = [
    pytest.mark.native_gui,
    pytest.mark.skipif(
        os.name != "nt" or os.environ.get("RACP_TEST_GUI") != "1",
        reason="opt-in owned Windows GUI fixture (RACP_TEST_GUI=1)",
    ),
]


class OwnedWindow:
    def __init__(self) -> None:
        self.ready = threading.Event()
        self.handle = self.edit = self.button = self.password = 0
        self.invocations = 0
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5) and self.error is None

    def run(self) -> None:
        import win32api
        import win32con
        import win32gui

        name = "RACP-Acceptance-" + secrets.token_hex(12)
        instance = win32api.GetModuleHandle(None)
        try:
            window_class = win32gui.WNDCLASS()
            window_class.hInstance, window_class.lpszClassName = instance, name
            window_class.hbrBackground = win32con.COLOR_WINDOW + 1

            def callback(handle: int, message: int, wparam: int, lparam: int) -> int:
                if message == win32con.WM_COMMAND and wparam & 0xFFFF == 102:
                    self.invocations += 1
                    return 0
                if message == win32con.WM_APP:
                    win32gui.SetFocus(self.edit)
                    return 0
                if message == win32con.WM_DESTROY:
                    win32gui.PostQuitMessage(0)
                    return 0
                return int(win32gui.DefWindowProc(handle, message, wparam, lparam))

            window_class.lpfnWndProc = callback
            win32gui.RegisterClass(window_class)
            self.handle = win32gui.CreateWindow(
                name,
                "RACP temporary acceptance window",
                win32con.WS_OVERLAPPEDWINDOW,
                100,
                100,
                480,
                300,
                0,
                0,
                instance,
                None,
            )
            self.edit = win32gui.CreateWindow(
                "EDIT",
                "",
                win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER,
                20,
                25,
                400,
                32,
                self.handle,
                101,
                instance,
                None,
            )
            self.button = win32gui.CreateWindow(
                "BUTTON",
                "Fixture Invoke",
                win32con.WS_CHILD | win32con.WS_VISIBLE,
                20,
                80,
                180,
                32,
                self.handle,
                102,
                instance,
                None,
            )
            self.password = win32gui.CreateWindow(
                "EDIT",
                "fixture secret",
                win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER | win32con.ES_PASSWORD,
                20,
                130,
                400,
                32,
                self.handle,
                103,
                instance,
                None,
            )
            win32gui.ShowWindow(self.handle, win32con.SW_SHOWNOACTIVATE)
            self.ready.set()
            win32gui.PumpMessages()
        except BaseException as exc:
            self.error = exc
            self.ready.set()
        finally:
            if self.handle and win32gui.IsWindow(self.handle):
                win32gui.DestroyWindow(self.handle)
            win32gui.UnregisterClass(name, instance)

    def close(self) -> None:
        import win32con
        import win32gui

        if self.handle and win32gui.IsWindow(self.handle):
            win32gui.PostMessage(self.handle, win32con.WM_CLOSE, 0, 0)
        self.thread.join(5)
        assert not self.thread.is_alive()


@pytest.fixture
def owned() -> Any:
    if process_identity(os.getpid()).session == 0:
        pytest.skip("requires a logged-on interactive session")
    window = OwnedWindow()
    backend = None
    try:
        backend = WindowsDesktop(process_identity(os.getpid()).session)
        if not backend.status()["available"]:
            pytest.skip("interactive desktop is unavailable")
        yield window, backend
    finally:
        if backend is not None:
            backend.close()
        window.close()


def require_foreground(window: OwnedWindow, backend: WindowsDesktop) -> None:
    import win32con
    import win32gui

    try:
        win32gui.ShowWindow(window.handle, win32con.SW_SHOW)
        win32gui.SetForegroundWindow(window.handle)
    except Exception:
        pass
    win32gui.PostMessage(window.handle, win32con.WM_APP, 0, 0)
    backend.watch.sync()
    if win32gui.GetForegroundWindow() != window.handle:
        pytest.skip("OS did not activate the owned fixture")


def wait_for(predicate: Any) -> None:
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def prepare(
    backend: WindowsDesktop, window: OwnedWindow
) -> tuple[BrokerCore, BrokerContext, str, str]:
    core = BrokerCore(backend.session_id, backend)
    context = BrokerContext(
        owner_id="owner_native", device_id="device_native", operation_id="op_native"
    )
    lease = core.execute("desktop.lease_acquire", {"session_id": backend.session_id}, context)
    return core, context, lease["lease_id"], str(backend.window(window.handle)["window_id"])


def payload(
    core: BrokerCore, context: BrokerContext, lease: str, window_id: str, **extra: Any
) -> dict[str, Any]:
    observation = core.execute("desktop.windows", {"session_id": core.session_id}, context)
    return {
        "session_id": core.session_id,
        "lease_id": lease,
        "expected_window_id": window_id,
        "observation_id": observation["observation_id"],
        "layout_revision": observation["layout_revision"],
        **extra,
    }


def test_owned_window_unicode_chord_drag_capture_and_foreign_input_abort(owned: Any) -> None:
    import win32gui

    window, backend = owned
    require_foreground(window, backend)
    core, context, lease, window_id = prepare(backend, window)
    try:
        before = backend.input_tick()
        assert 0 < backend.tag < 2**31  # The actual mouse echo must preserve the whole marker.
        outcome = core.execute(
            "desktop.type", payload(core, context, lease, window_id, text="RACP 한글 😀"), context
        )
        assert outcome["verification"] == "os_dispatch_only"
        wait_for(lambda: win32gui.GetWindowText(window.edit) == "RACP 한글 😀")
        assert backend.input_tick() == before  # Real low-level hooks exclude our tagged dispatch.
        core.execute(
            "desktop.key", payload(core, context, lease, window_id, keys=["CTRL", "A"]), context
        )
        core.execute(
            "desktop.key", payload(core, context, lease, window_id, keys=["DELETE"]), context
        )
        wait_for(lambda: win32gui.GetWindowText(window.edit) == "")
        point = win32gui.ClientToScreen(window.handle, (240, 200))
        core.execute(
            "desktop.drag",
            payload(
                core,
                context,
                lease,
                window_id,
                x=point[0],
                y=point[1],
                end_x=point[0] + 50,
                end_y=point[1],
                duration_ms=80,
            ),
            context,
        )
        assert not backend.pressed and not backend.user.GetAsyncKeyState(1) & 0x8000
        image = core.execute(
            "desktop.screenshot", {"session_id": core.session_id, "window_id": window_id}, context
        )
        rectangle = backend.window(window.handle)["bounds"]
        assert image["capture_scope"] == "visible_rectangle"
        assert image["width"] == rectangle[2] - rectangle[0]
        assert image["height"] == rectangle[3] - rectangle[1]
        assert image["capture"]["size_bytes"] > 0 and image["preview"]["capture"]["size_bytes"] > 0
        # An unmarked Shift down/up batch has no text effect and always produces hook events.
        assert win32gui.GetForegroundWindow() == window.handle
        down, up = backend.key(vk=16), backend.key(vk=16, flags=2)
        down.value.key.extra = up.value.key.extra = 0
        buffer = (Input * 2)(down, up)
        assert backend.user.SendInput(2, buffer, ctypes.sizeof(Input)) == 2
        backend.watch.sync()
        wait_for(lambda: backend.input_tick() > before)
        with pytest.raises(RACPError) as changed:
            core.execute(
                "desktop.type", payload(core, context, lease, window_id, text="blocked"), context
            )
        assert changed.value.error.code in {"STALE_OBSERVATION", "PERMISSION_DENIED"}
        assert core.lease is None and win32gui.GetWindowText(window.edit) == ""
    finally:
        core.abort()


def test_owned_window_ui_automation_value_invoke_stale_reference_and_password(owned: Any) -> None:
    import win32gui

    window, backend = owned
    require_foreground(window, backend)
    assert backend.status()["ui_automation_available"]
    core, context, lease, window_id = prepare(backend, window)

    def observed() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        result = core.execute(
            "desktop.inspect", {"session_id": core.session_id, "window_id": window_id}, context
        )
        common = {
            "session_id": core.session_id,
            "lease_id": lease,
            "expected_window_id": window_id,
            "observation_id": result["observation_id"],
            "layout_revision": result["layout_revision"],
        }
        return common, result["elements"]

    try:
        common, elements = observed()
        edit = next(element for element in elements if element["automation_id"] == "101")
        assert "value" in edit["patterns"]
        result = core.execute(
            "desktop.set_value",
            {**common, "element_ref": edit["element_ref"], "value": "UIA 한글"},
            context,
        )
        assert result["pattern"] == "value" and result["verification"] == "os_dispatch_only"
        assert win32gui.GetWindowText(window.edit) == "UIA 한글"
        with pytest.raises(RACPError):
            core.execute(
                "desktop.set_value",
                {**common, "element_ref": edit["element_ref"], "value": "stale"},
                context,
            )
        assert win32gui.GetWindowText(window.edit) == "UIA 한글"
        # Reacquire after the deliberately stale request invalidated the lease.
        lease = core.execute("desktop.lease_acquire", {"session_id": core.session_id}, context)[
            "lease_id"
        ]
        common, elements = observed()
        button = next(element for element in elements if element["name"] == "Fixture Invoke")
        assert "invoke" in button["patterns"]
        core.execute("desktop.invoke", {**common, "element_ref": button["element_ref"]}, context)
        wait_for(lambda: window.invocations == 1)
        common, elements = observed()
        password = next(element for element in elements if element["password"])
        assert password["name"] == "" and password["patterns"] == []
        with pytest.raises(RACPError) as denied:
            core.execute(
                "desktop.set_value",
                {**common, "element_ref": password["element_ref"], "value": "blocked"},
                context,
            )
        assert denied.value.error.code == "PERMISSION_DENIED"
    finally:
        core.abort()


@pytest.mark.desktop
async def test_owned_window_real_ui_automation_tree_native_destroy_and_focus_refusal(
    owned: Any, live: dict[str, Any]
) -> None:
    import win32gui

    window, backend = owned
    assert backend.status()["ui_automation_available"]
    core, context, lease, window_id = prepare(backend, window)
    try:
        result = core.execute(
            "desktop.inspect", {"session_id": core.session_id, "window_id": window_id}, context
        )
        elements = result["elements"]
        assert not result["truncated"] and len(elements) <= 64
        edit = next(element for element in elements if element["automation_id"] == "101")
        button = next(element for element in elements if element["name"] == "Fixture Invoke")
        password = next(element for element in elements if element["password"])
        assert "value" in edit["patterns"] and "invoke" in button["patterns"]
        assert password["name"] == "" and not password["patterns"]

        # These calls use a separate real Broker, authenticated pipe, Agent journal and HTTP/MCP.
        async def operation(name: str, data: dict[str, Any]) -> dict[str, Any]:
            accepted = await live["client"].post(
                "/api/v1/operations",
                json={"device_id": live["device_id"], "operation": name, "payload": data},
            )
            assert accepted.status_code < 400, accepted.text
            identifier = accepted.json()["operation_id"]
            for _ in range(200):
                outcome = (await live["client"].get("/api/v1/operations/" + identifier)).json()
                if outcome["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
                    assert outcome["state"] == "SUCCEEDED", outcome
                    return dict(outcome["result"])
                await asyncio.sleep(0.025)
            pytest.fail("native observation did not finish")

        remote_windows = await operation("desktop.windows", {"session_id": core.session_id})
        remote = next(
            w
            for w in remote_windows["windows"]
            if w["pid"] == os.getpid() and w["title"] == "RACP temporary acceptance window"
        )
        remote_tree = await operation(
            "desktop.inspect", {"session_id": core.session_id, "window_id": remote["window_id"]}
        )
        assert any(
            e["automation_id"] == "101" and "value" in e["patterns"]
            for e in remote_tree["elements"]
        )
        denied = await live["client"].post(
            "/api/v1/operations",
            json={
                "device_id": live["device_id"],
                "operation": "desktop.invoke",
                "payload": {
                    "session_id": core.session_id,
                    "lease_id": "lease_ungranted",
                    "observation_id": remote_tree["observation_id"],
                    "layout_revision": remote_tree["layout_revision"],
                    "expected_window_id": remote["window_id"],
                    "element_ref": "element_ungranted",
                },
                "idempotency_key": "denied-uia-read-only",
            },
        )
        assert denied.status_code == 403 and window.invocations == 0
        import httpx
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
            async with Client(
                streamable_http_client(live["url"] + "/mcp/", http_client=http)
            ) as client:
                content = await client.call_tool(
                    "desktop_inspect",
                    {
                        "device_id": live["device_id"],
                        "session_id": core.session_id,
                        "window_id": remote["window_id"],
                    },
                )
                assert not content.is_error and content.structured_content is not None
                assert "element_ref" in str(content.structured_content)
        if win32gui.GetForegroundWindow() != window.handle:
            with pytest.raises(RACPError) as refused:
                core.execute(
                    "desktop.set_value",
                    {
                        "session_id": core.session_id,
                        "lease_id": lease,
                        "observation_id": result["observation_id"],
                        "layout_revision": result["layout_revision"],
                        "expected_window_id": window_id,
                        "element_ref": edit["element_ref"],
                        "value": "must not dispatch",
                    },
                    context,
                )
            # Read-side tick may already have revoked the lease after an intervening
            # foreground/input event; either path must refuse before touching the control.
            assert refused.value.error.code in {"STALE_OBSERVATION", "PERMISSION_DENIED"}
            assert win32gui.GetWindowText(window.edit) == "" and core.lease is None
        generation = backend.watch.counter.generation(window.handle)
        window.close()
        backend.watch.sync()
        wait_for(lambda: backend.watch.counter.generation(window.handle) != generation)
        assert not backend.validate_window({"window_id": window_id})
    finally:
        core.abort()


def test_session_input_mutex_excludes_a_separate_broker_process(owned: Any) -> None:
    window, backend = owned
    core, context, _, _ = prepare(backend, window)
    code = (
        "import os\n"
        "from racp_agent.broker.identity import process_identity\n"
        "from racp_agent.broker.input_lease import SessionInputLease\n"
        "from racp_domain.models import RACPError\n"
        "identity=process_identity(os.getpid())\n"
        "lease=SessionInputLease(identity.session,identity.sid)\n"
        "try:\n"
        " try: lease.acquire()\n"
        " except RACPError as e: print(e.error.code)\n"
        " else: print('acquired')\n"
        "finally: lease.close()\n"
    )
    try:
        blocked = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=5
        )
        assert blocked.returncode == 0 and blocked.stdout.strip() == "RESOURCE_BUSY", blocked.stderr
        core.abort()
        acquired = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=5
        )
        assert acquired.returncode == 0 and acquired.stdout.strip() == "acquired", acquired.stderr
    finally:
        core.abort()


@pytest.mark.parametrize("cause", ["job_termination", "hold_timeout", "rpc_cancel"])
async def test_independent_guardian_releases_owned_modifier_and_button_after_broker_exit(
    owned: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    import win32gui
    from racp_agent.broker.guard_client import guard_request
    from racp_agent.broker.supervisor import BrokerSupervisor

    window, backend = owned
    require_foreground(window, backend)
    script = Path(__file__).parents[1] / "fixtures/native_guarded_broker.py"
    monkeypatch.setattr(
        BrokerSupervisor,
        "_command",
        lambda self, path: [sys.executable, "-I", str(script), "--pairing", str(path)],
    )
    broker = BrokerSupervisor(tmp_path / "broker", backend.session_id, "dev_guard_native")
    work = None
    try:
        await broker.start()
        assert broker.guardian is not None and broker.status["input_guardian_available"]
        guardian = broker.guardian
        assert (await asyncio.to_thread(guard_request, guardian.config, "guard.status"))[
            "outside_broker_job"
        ]
        context = {
            "owner_id": "owner_native",
            "device_id": "dev_guard_native",
            "operation_id": "op_guard_native",
            "timeout_ms": 10000,
        }
        lease = await broker.call(
            "desktop.lease_acquire", {"session_id": backend.session_id}, context, 3
        )
        observed = await broker.call(
            "desktop.windows", {"session_id": backend.session_id}, context, 3
        )
        target = next(
            w
            for w in observed["windows"]
            if w["pid"] == os.getpid() and w["title"] == "RACP temporary acceptance window"
        )
        common = {
            "session_id": backend.session_id,
            "lease_id": lease["lease_id"],
            "observation_id": observed["observation_id"],
            "layout_revision": observed["layout_revision"],
            "expected_window_id": target["window_id"],
        }
        point = win32gui.ClientToScreen(window.handle, (260, 190))
        await broker.call("desktop.move", {**common, "x": point[0], "y": point[1]}, context, 3)
        observed = await broker.call(
            "desktop.windows", {"session_id": backend.session_id}, context, 3
        )
        common["observation_id"] = observed["observation_id"]
        if cause == "rpc_cancel":
            work = asyncio.create_task(
                broker.rpc(
                    "desktop.type",
                    {**common, "text": "fixture_hold_for_kill"},
                    context,
                    time.monotonic() + 10,
                )
            )
        else:
            work = asyncio.create_task(
                broker.call(
                    "desktop.type", {**common, "text": "fixture_hold_for_kill"}, context, 10
                )
            )
        for _ in range(200):
            state = await asyncio.to_thread(guard_request, guardian.config, "guard.status")
            if state["held_count"] == 2:
                break
            await asyncio.sleep(0.01)
        assert state["held_count"] == 2 and state["armed"]
        assert (
            backend.user.GetAsyncKeyState(0xA2) & 0x8000
            and backend.user.GetAsyncKeyState(1) & 0x8000
        )
        pinned = guardian.peer
        if cause == "job_termination":
            await broker.finish_stop()
        elif cause == "rpc_cancel":
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
        else:
            await asyncio.wait_for(pinned.wait(), 7)
            await broker.finish_stop()
        assert broker.cleanup_status == "complete" and pinned.returncode == 0
        assert not backend.user.GetAsyncKeyState(0xA2) & 0x8000
        assert not backend.user.GetAsyncKeyState(1) & 0x8000
        assert backend.input_lease.gate.ready()
        # The session can be leased again only after independent cleanup completed.
        backend.acquire_lease()
        backend.release_lease()
    finally:
        await broker.finish_stop()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)


async def test_actual_agent_crash_releases_held_input_and_guardian_cleans_its_task(
    owned: Any, tmp_path: Path
) -> None:
    from test_guardian_agent_crash import assert_agent_crash

    window, backend = owned
    require_foreground(window, backend)
    await assert_agent_crash(tmp_path, os.getpid(), backend)


@pytest.fixture
def native_hold_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from racp_agent.broker.supervisor import BrokerSupervisor

    script = Path(__file__).parents[1] / "fixtures/native_guarded_broker.py"
    monkeypatch.setattr(
        BrokerSupervisor,
        "_command",
        lambda self, path: [sys.executable, "-I", str(script), "--pairing", str(path)],
    )


@pytest.fixture
def native_hold_live(native_hold_command: None, live: dict[str, Any]) -> dict[str, Any]:
    return live


@pytest.mark.desktop
@pytest.mark.parametrize("cause", ["operation_cancel", "device_revoke"])
async def test_actual_agent_wire_cancel_or_revoke_releases_held_input(
    owned: Any, native_hold_live: dict[str, Any], cause: str
) -> None:
    import win32gui
    from racp_agent.broker.guard_client import guard_request
    from test_desktop_provider import execute

    window, backend = owned
    require_foreground(window, backend)
    live = native_hold_live
    broker = live["agent"].desktop.sessions[backend.session_id]
    assert broker.guardian is not None
    guardian = broker.guardian
    lease = (
        await execute(
            live,
            "desktop.lease_acquire",
            {"session_id": backend.session_id},
            "lease-native-hold",
            "trusted_personal",
        )
    )["result"]
    observed = (await execute(live, "desktop.windows", {"session_id": backend.session_id}))[
        "result"
    ]
    target = next(
        w
        for w in observed["windows"]
        if w["pid"] == os.getpid() and w["title"] == "RACP temporary acceptance window"
    )
    common = {
        "session_id": backend.session_id,
        "lease_id": lease["lease_id"],
        "observation_id": observed["observation_id"],
        "layout_revision": observed["layout_revision"],
        "expected_window_id": target["window_id"],
    }
    point = win32gui.ClientToScreen(window.handle, (260, 190))
    assert (
        await execute(
            live,
            "desktop.move",
            {**common, "x": point[0], "y": point[1]},
            "move-native-hold",
            "trusted_personal",
        )
    )["state"] == "SUCCEEDED"
    observed = (await execute(live, "desktop.windows", {"session_id": backend.session_id}))[
        "result"
    ]
    common["observation_id"] = observed["observation_id"]
    accepted = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "desktop.type",
            "execution_mode": "job",
            "execution_profile_id": "trusted_personal",
            "idempotency_key": "native-hold-wire",
            "timeout_ms": 10000,
            "payload": {**common, "text": "fixture_hold_for_kill"},
        },
    )
    assert accepted.status_code == 202
    identifier = accepted.json()["operation_id"]
    for _ in range(200):
        state = await asyncio.to_thread(guard_request, guardian.config, "guard.status")
        if state["held_count"] == 2:
            break
        await asyncio.sleep(0.01)
    assert state["held_count"] == 2
    if cause == "operation_cancel":
        assert (
            await live["client"].post("/api/v1/operations/" + identifier + "/cancel")
        ).status_code < 400
    else:
        assert (
            await live["client"].post("/api/v1/devices/" + live["device_id"] + "/revoke")
        ).status_code < 400
    for _ in range(500):
        if broker.process is None and broker.guardian is None:
            break
        await asyncio.sleep(0.01)
    assert (
        broker.process is None and broker.guardian is None and broker.cleanup_status == "complete"
    )
    assert not backend.user.GetAsyncKeyState(0xA2) & 0x8000
    assert not backend.user.GetAsyncKeyState(1) & 0x8000
    assert backend.input_lease.gate.ready()
    if cause == "operation_cancel":
        for _ in range(100):
            result = (await live["client"].get("/api/v1/operations/" + identifier)).json()
            if result["state"] == "CANCELLED":
                break
            await asyncio.sleep(0.02)
        assert (
            result["state"] == "CANCELLED"
            and result["error"]["details"]["cleanup_status"] == "complete"
        )


@pytest.mark.desktop
async def test_public_guarded_drag_through_http_moves_only_on_owned_fixture(
    owned: Any, live: dict[str, Any]
) -> None:
    import win32gui
    from test_desktop_provider import execute

    window, backend = owned
    require_foreground(window, backend)
    sessions = await execute(live, "desktop.sessions", {})
    assert sessions["state"] == "SUCCEEDED"
    assert "desktop.drag" in live["agent"].desktop.capability().operations
    lease = (
        await execute(
            live,
            "desktop.lease_acquire",
            {"session_id": backend.session_id},
            "lease-public-drag",
            "trusted_personal",
        )
    )["result"]
    observed = (await execute(live, "desktop.windows", {"session_id": backend.session_id}))[
        "result"
    ]
    target = next(
        w
        for w in observed["windows"]
        if w["pid"] == os.getpid() and w["title"] == "RACP temporary acceptance window"
    )
    point = win32gui.ClientToScreen(window.handle, (260, 190))
    outcome = await execute(
        live,
        "desktop.drag",
        {
            "session_id": backend.session_id,
            "lease_id": lease["lease_id"],
            "observation_id": observed["observation_id"],
            "layout_revision": observed["layout_revision"],
            "expected_window_id": target["window_id"],
            "x": point[0],
            "y": point[1],
            "end_x": point[0] + 40,
            "end_y": point[1],
            "duration_ms": 80,
        },
        "public-drag-native",
        "trusted_personal",
    )
    assert outcome["state"] == "SUCCEEDED", outcome
    assert outcome["result"]["verification"] == "os_dispatch_only"
    assert not backend.user.GetAsyncKeyState(1) & 0x8000 and backend.input_lease.gate.ready()
