"""Release-only companion health and observed-input receipt, using existing bounded local IPC."""

import os
import time
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import new_id

from racp_agent.broker.guard_config import GuardConfig
from racp_agent.broker.identity import process_identity
from racp_agent.broker.peer_process import PeerProcess
from racp_agent.broker.pipe import request


def guard_request(
    config: GuardConfig, operation: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    reply = request(
        config,
        {
            "operation": operation,
            "payload": payload or {},
            "context": {
                "owner_id": "owner_local",
                "device_id": "device_local",
                "operation_id": new_id("op"),
                "timeout_ms": 1000,
            },
        },
        1,
    )
    if reply.get("state") != "SUCCEEDED" or not isinstance(reply.get("result"), dict):
        raise RACPError(
            "SESSION_UNAVAILABLE", "input guardian rejected the request", layer="broker"
        )
    return dict(reply["result"])


def pin_guard(config: GuardConfig) -> PeerProcess:
    import win32api
    import win32job

    if config.guardian_pid is None:
        raise PermissionError("input guardian has no verified process identity")
    peer = process_identity(config.guardian_pid)
    config.require_broker(peer)
    handle = win32api.OpenProcess(0x101000, False, peer.pid)
    try:
        # Recheck after pinning and reject every inherited job, not just this Broker job.
        config.require_broker(process_identity(peer.pid))
        if config.launch_backend == "direct" and win32job.IsProcessInJob(handle, None):
            raise PermissionError("direct input guardian inherited a Job")
        job = win32job.OpenJobObject(4, False, config.job_name) if config.job_name else None
        try:
            if win32job.IsProcessInJob(handle, job):
                raise PermissionError("input guardian is contained in its Broker job")
        finally:
            if job is not None:
                job.Close()
    except BaseException:
        handle.Close()
        raise
    return PeerProcess(peer.pid, handle)


class GuardClient:
    def __init__(self, config: GuardConfig) -> None:
        parent = process_identity(os.getpid())
        if (parent.pid, parent.sid, parent.session) != (
            config.agent_pid,
            config.user_sid,
            config.session_id,
        ) or abs(parent.created - config.agent_created) > 0.000001:
            raise PermissionError("input guardian parent scope mismatch")
        self.config = config
        self.peer = pin_guard(config)
        try:
            self.before_send()
        except BaseException:
            self.peer.close()
            raise

    def before_send(self) -> int:
        if self.peer.returncode is not None:
            raise RACPError("SESSION_UNAVAILABLE", "input guardian exited", layer="broker")
        state = guard_request(self.config, "guard.status")
        if not state.get("healthy") or state.get("closing"):
            raise RACPError("SESSION_UNAVAILABLE", "input guardian unavailable", layer="broker")
        return int(state["own_sequence"])

    def arm(self) -> int:
        self.before_send()
        return int(guard_request(self.config, "guard.arm")["own_sequence"])

    def idle(self) -> None:
        deadline = time.monotonic() + 0.2
        while guard_request(self.config, "guard.status")["held_count"]:
            if time.monotonic() >= deadline:
                raise RACPError(
                    "INPUT_DISPATCH_FAILED",
                    "input guardian still owns pressed input",
                    layer="broker",
                    execution_state="unknown",
                    cleanup_status="unverified",
                )
            time.sleep(0.005)
        guard_request(self.config, "guard.idle")

    def observed(self, before: int, count: int) -> None:
        deadline = time.monotonic() + 0.15
        while True:
            current = self.before_send()
            if current - before >= count:
                return
            if time.monotonic() >= deadline:
                raise RACPError(
                    "INPUT_DISPATCH_FAILED",
                    "input guardian dispatch receipt missing",
                    layer="broker",
                    execution_state="unknown",
                )
            time.sleep(0.005)

    def close(self) -> None:
        try:
            if self.peer.returncode is None:
                guard_request(self.config, "guard.close")
        finally:
            self.peer.close()
