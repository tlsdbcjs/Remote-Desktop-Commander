"""Start a known local guardian outside inherited jobs; bootstrap never accepts executable argv."""

import json
import subprocess
import sys
import sysconfig
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError

from racp_agent.broker.guard_client import guard_request, pin_guard
from racp_agent.broker.guard_config import GuardConfig
from racp_agent.broker.guard_task import ScheduledGuard, start_scheduled
from racp_agent.broker.peer_process import PeerProcess
from racp_agent.broker.pipe import NativeError
from racp_agent.providers.shell import execution_env


@dataclass
class GuardProcess:
    config: GuardConfig
    process: subprocess.Popen[bytes] | None
    peer: PeerProcess
    scheduled: ScheduledGuard | None = None

    def close(self) -> bool:
        exited = False
        try:
            if self.peer.returncode is None:
                try:
                    guard_request(self.config, "guard.close")
                except (RACPError, OSError, NativeError, TimeoutError, PermissionError):
                    pass  # Parent death may already have closed IPC; verify native exit below.
            deadline = time.monotonic() + 6
            while self.peer.returncode is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("input guardian cleanup timed out")
                time.sleep(0.01)
            exited = True
            return (self.process is None or self.process.wait(6) == 0) and self.peer.returncode == 0
        finally:
            self.peer.close()
            if self.process is not None and self.process.stdout:
                self.process.stdout.close()
            if self.scheduled is not None and exited:
                self.scheduled.cleanup()


def start_guard(config: GuardConfig) -> GuardProcess:
    try:
        return start_direct(config)
    except (OSError, PermissionError, TimeoutError, ValueError):
        ready, peer, scheduled = start_scheduled(config)
        return GuardProcess(ready, None, peer, scheduled)


def start_direct(config: GuardConfig) -> GuardProcess:
    process = subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-I",
            str(Path(__file__).with_name("guard_bootstrap.py")),
        ],
        env=execution_env({}),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=0x09000000,  # NO_WINDOW | BREAKAWAY_FROM_JOB
    )
    peer = None
    try:
        assert process.stdin is not None and process.stdout is not None
        bootstrap = {"site": sysconfig.get_path("purelib"), "config": config.model_dump()}
        raw = json.dumps(bootstrap).encode() + b"\n"
        if len(raw) > 65536:
            raise ValueError("guardian bootstrap exceeds limit")
        process.stdin.write(raw)
        process.stdin.close()
        result: list[Any] = []
        output = process.stdout

        def read() -> None:
            try:
                result.append(output.readline(65537))
            except BaseException as exc:
                result.append(exc)

        reader = threading.Thread(target=read, name="racp-guardian-bootstrap", daemon=True)
        reader.start()
        reader.join(5)
        if reader.is_alive() or not result or isinstance(result[0], BaseException):
            raise TimeoutError("input guardian readiness unavailable")
        ready = GuardConfig.model_validate(json.loads(result[0])["ready"])
        if ready.guardian_pid != process.pid or ready.model_dump(
            exclude={"guardian_pid", "guardian_created"}
        ) != config.model_dump(exclude={"guardian_pid", "guardian_created"}):
            raise PermissionError("input guardian bootstrap identity mismatch")
        peer = pin_guard(ready)
        state = guard_request(ready, "guard.status")
        if not state.get("healthy") or not state.get("outside_broker_job"):
            raise PermissionError("input guardian not ready")
        return GuardProcess(ready, process, peer)
    except BaseException:
        # The stdlib base process is also the guardian; no redirector child can escape.
        if process.poll() is None:
            process.kill()
        process.wait(5)
        if peer is not None:
            peer.close()
        if process.stdout is not None:
            process.stdout.close()
        raise
