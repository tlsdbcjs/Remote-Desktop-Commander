"""Independent, release-only watchdog. It is deliberately outside every Windows Job."""

import ctypes
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from typing import Any

from pydantic import ValidationError
from racp_domain.models import RACPError

from racp_agent.broker.access import grant_own_access
from racp_agent.broker.core import BrokerRequest
from racp_agent.broker.guard_config import Controller, GuardConfig
from racp_agent.broker.guard_ledger import HeldInputs
from racp_agent.broker.identity import process_identity
from racp_agent.broker.input_watch import InputWatch
from racp_agent.broker.pipe import PipeServer
from racp_agent.broker.release_gate import ReleaseGate

windows_ctypes: Any = ctypes


class InputGuardian:
    def __init__(self, config: GuardConfig) -> None:
        import win32api
        import win32job

        config.require_broker(process_identity(os.getpid()))
        if config.launch_backend == "direct" and win32job.IsProcessInJob(
            win32api.GetCurrentProcess(), None
        ):
            raise PermissionError("direct input guardian inherited a host Job")
        if config.job_name is not None:
            job = win32job.OpenJobObject(4, False, config.job_name)
            try:
                parent_check = win32api.OpenProcess(0x101000, False, config.agent_pid)
                try:
                    if not win32job.IsProcessInJob(parent_check, job):
                        raise PermissionError(
                            "input guardian parent is not in the configured Broker job"
                        )
                finally:
                    parent_check.Close()
                if win32job.IsProcessInJob(win32api.GetCurrentProcess(), job):
                    raise PermissionError("input guardian must be outside its Broker job")
            finally:
                job.Close()
        elif win32job.IsProcessInJob(win32api.GetCurrentProcess(), None):
            raise PermissionError("input guardian has no independently verifiable Broker job")
        self.config, self.ledger = config, HeldInputs()
        self.parent = win32api.OpenProcess(0x101001, False, config.agent_pid)
        try:
            # Pin before identity checks, so PID reuse can never redirect termination.
            parent = process_identity(config.agent_pid)
            if (parent.sid, parent.session) != (config.user_sid, config.session_id) or abs(
                parent.created - config.agent_created
            ) > 0.000001:
                raise PermissionError("input guardian parent identity mismatch")
            self.watch = InputWatch(config.marker, own_keyboard=self.keyboard, own_mouse=self.mouse)
        except BaseException:
            self.parent.Close()
            raise
        self.baseline_foreign = self.baseline_foreground = 0
        self.gate = ReleaseGate(config.session_id, config.user_sid)
        self.armed_since: float | None = None
        self.closing, self.kill_requested = False, False
        self.done = threading.Event()
        self.cleanup_status = "pending"
        self.reason = "active"
        self.monitor = threading.Thread(
            target=self.watch_parent, name="racp-release-watchdog", daemon=True
        )
        self.monitor.start()

    def baseline(self) -> None:
        if not self.ledger.pending:
            self.baseline_foreign = self.watch.counter.sequence
            self.baseline_foreground = self.watch.counter.foreground_sequence

    def keyboard(self, vk: int, scan: int, flags: int) -> None:
        if not flags & 0x80:
            self.baseline()
        self.ledger.keyboard(vk, scan, flags)

    def mouse(self, message: int, data: int) -> None:
        if message in {0x201, 0x204, 0x207}:
            self.baseline()
        self.ledger.mouse(message, data)

    def status(self) -> dict[str, Any]:
        self.watch.sync()
        return {
            "healthy": self.watch.healthy() and not self.ledger.failure and not self.done.is_set(),
            "closing": self.closing,
            "own_sequence": self.watch.counter.own_sequence,
            "held_count": len(self.ledger.pending),
            "cleanup_status": self.cleanup_status,
            "max_hold_ms": self.config.max_hold_ms,
            "outside_broker_job": True,
            "independence_scope": "broker_job" if self.config.job_name else "all_jobs",
            "armed": self.armed_since is not None,
            "guardian_pid": self.config.guardian_pid,
            "guardian_created": self.config.guardian_created,
        }

    def handle(self, raw: dict[str, Any]) -> dict[str, Any]:
        try:
            request = BrokerRequest.model_validate(raw)
            if request.operation == "guard.status" and not request.payload:
                result = self.status()
            elif (
                request.operation == "guard.arm"
                and not request.payload
                and self.config.parent_caller()
            ):
                state = self.status()
                if not state["healthy"] or self.closing:
                    raise RACPError(
                        "SESSION_UNAVAILABLE", "input guardian unavailable", layer="broker"
                    )
                self.gate.arm()
                if self.armed_since is None:
                    self.armed_since = time.monotonic()
                result = self.status()
            elif (
                request.operation == "guard.idle"
                and not request.payload
                and self.config.parent_caller()
            ):
                state = self.status()
                if state["held_count"] or not state["healthy"]:
                    raise RACPError(
                        "RESOURCE_BUSY", "owned inputs are still pending", layer="broker"
                    )
                self.armed_since = None
                self.gate.release()
                result = state
            elif request.operation == "guard.bind_controller" and self.config.parent_caller():
                controller = Controller.model_validate(request.payload)
                identity = process_identity(controller.pid)
                controller.require(identity)
                if (controller.service_sid or controller.sid) != self.config.controller_principal:
                    raise PermissionError("input guardian controller ACL mismatch")
                if self.config.controller is not None and self.config.controller != controller:
                    raise PermissionError("input guardian controller already bound")
                self.config.controller = controller
                result = {"bound": True}
            elif request.operation == "guard.close" and not request.payload:
                self.kill_requested = not self.config.parent_caller()
                self.closing = True
                result = {"closing": True}
            else:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE",
                    "guardian permits only status/bind/close",
                    layer="broker",
                )
            return {"state": "SUCCEEDED", "result": result, "error": None}
        except (ValidationError, PermissionError):
            error = RACPError(
                "PERMISSION_DENIED", "input guardian request rejected", layer="broker"
            )
            return {"state": "FAILED", "result": None, "error": asdict(error.error)}
        except RACPError as exc:
            return {"state": "FAILED", "result": None, "error": asdict(exc.error)}

    def release(self) -> bool:
        from racp_agent.broker.windows import Input, InputUnion, KeyInput, MouseInput

        pending = self.ledger.snapshot()
        if not pending:
            return True
        events = []
        for item in pending:
            value = (
                InputUnion(key=KeyInput(item.code, item.scan, item.flags, 0, self.config.marker))
                if item.kind == "key"
                else InputUnion(mouse=MouseInput(0, 0, 0, item.flags, 0, self.config.marker))
            )
            events.append(Input(1 if item.kind == "key" else 0, value))
        user = windows_ctypes.WinDLL("user32", use_last_error=True)
        user.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int]
        user.SendInput.restype = ctypes.c_uint
        buffer = (Input * len(events))(*events)
        return int(user.SendInput(len(events), buffer, ctypes.sizeof(Input))) == len(events)

    def watch_parent(self) -> None:
        import win32api
        import win32event

        try:
            while not self.closing:
                if win32event.WaitForSingleObject(self.parent, 0) == 0:
                    self.reason = "parent_exited"
                    break
                interrupted = bool(self.ledger.pending) and (
                    self.watch.counter.sequence != self.baseline_foreign
                    or self.watch.counter.foreground_sequence != self.baseline_foreground
                )
                armed_overdue = (
                    self.armed_since is not None
                    and time.monotonic() - self.armed_since >= self.config.max_hold_ms / 1000
                )
                if (
                    self.ledger.overdue(self.config.max_hold_ms / 1000)
                    or armed_overdue
                    or interrupted
                    or not self.watch.healthy()
                ):
                    self.reason = "held_input_interrupted" if interrupted else "held_input_timeout"
                    self.kill_requested = True
                    break
                time.sleep(0.01)
            if self.kill_requested and win32event.WaitForSingleObject(self.parent, 0) != 0:
                win32api.TerminateProcess(self.parent, 1)
                if win32event.WaitForSingleObject(self.parent, 5000) != 0:
                    raise OSError("input guardian parent termination unverified")
            # Drain late hook delivery after parent exit. Never stop listening immediately
            # at the process signal: an already queued input batch can still be delivered.
            deadline, quiet_since, sequence = time.monotonic() + 5, time.monotonic(), -1
            while time.monotonic() < deadline:
                self.watch.sync()
                if not self.release():
                    raise OSError("input guardian release rejected by OS")
                if self.watch.counter.own_sequence != sequence or self.ledger.pending:
                    quiet_since, sequence = time.monotonic(), self.watch.counter.own_sequence
                if not self.ledger.pending and time.monotonic() - quiet_since >= 0.15:
                    self.cleanup_status = "complete" if not self.ledger.failure else "unverified"
                    if self.cleanup_status == "complete":
                        self.gate.release()
                    return
                time.sleep(0.01)
            raise OSError("input guardian input release unverified")
        except Exception:
            self.cleanup_status = "unverified"
        finally:
            self.closing = True
            self.done.set()

    def close(self) -> None:
        self.closing = True
        self.monitor.join(6)
        try:
            self.watch.close()
        finally:
            self.parent.Close()
            self.gate.close()


def run(config: GuardConfig, *, report_ready: bool = True) -> None:
    identity = process_identity(os.getpid())
    config = config.model_copy(
        update={"guardian_pid": identity.pid, "guardian_created": identity.created}
    )
    restore = grant_own_access((config.controller_principal,), managed_broker=False)
    guardian = server = None
    try:
        guardian = InputGuardian(config)
        server = PipeServer(config)
        if report_ready:
            print(json.dumps({"ready": config.model_dump()}), flush=True)
        while not guardian.done.is_set():
            server.accept(guardian.handle)
    finally:
        try:
            if server is not None:
                server.close()
            if guardian is not None:
                guardian.close()
        finally:
            restore()
    raise SystemExit(0 if guardian.cleanup_status == "complete" else 2)


def main() -> None:
    # Configuration is anonymous stdin only. It contains no owner/Device credentials.
    raw = sys.stdin.buffer.readline(65537)
    if not 0 < len(raw) <= 65536:
        raise ValueError("invalid input guardian bootstrap")
    run(GuardConfig.model_validate_json(raw))


if __name__ == "__main__":
    main()
