"""Agent-owned foreground Broker lifetime, containment, serialized RPC and crash backoff."""

import asyncio
import json
import os
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import new_id

from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.guard_client import pin_guard
from racp_agent.broker.guard_config import Controller, GuardConfig
from racp_agent.broker.guard_spawn import GuardProcess, start_guard
from racp_agent.broker.identity import process_identity
from racp_agent.broker.job import create_broker_job
from racp_agent.broker.peer_process import PeerProcess
from racp_agent.broker.pipe import NativeError, request
from racp_agent.providers.shell import execution_env


class BrokerSupervisor:
    def __init__(self, root: Path, session_id: int, device_id: str) -> None:
        self.root, self.session_id, self.device_id = root, session_id, device_id
        self.config: BrokerConfig | None = None
        self.path: Path | None = None
        self.process: asyncio.subprocess.Process | PeerProcess | None = None
        self.login_managed = False
        self.job: Any = None
        self.lock = asyncio.Lock()
        self.next_start, self.backoff = 0.0, 0.5
        self.status: dict[str, Any] = {"available": False, "reason": "Broker not started"}
        self.cleanup_status = "complete"
        self.guardian: GuardProcess | None = None

    def private_context(self, timeout_ms: int = 3000) -> dict[str, Any]:
        return {
            "owner_id": "owner_local",
            "device_id": self.device_id,
            "operation_id": new_id("op"),
            "timeout_ms": timeout_ms,
        }

    def _command(self, path: Path) -> list[str]:
        return [sys.executable, "-I", "-m", "racp_agent.broker.main", "--pairing", str(path)]

    async def adopt(
        self,
        config: BrokerConfig,
        process: PeerProcess,
        job: Any,
        on_commit: Callable[[], None] | None = None,
    ) -> None:
        async with self.lock:
            if self.process is not None and self.process.returncode is None:
                raise RACPError("RESOURCE_BUSY", "session already has a Broker", layer="agent")
            await self.stop()
            if on_commit is not None:
                on_commit()
            self.config, self.process, self.job = config, process, job
            self.login_managed = True
            self.cleanup_status = "complete"
            self.status = {
                "available": False,
                "session_id": self.session_id,
                "user_sid": config.user_sid,
                "reason": "Broker registration initializing",
            }

    async def start(self) -> None:
        if os.name != "nt":
            raise RACPError("CAPABILITY_UNAVAILABLE", "desktop requires Windows", layer="agent")
        if self.process is not None and self.process.returncode is None:
            if self.login_managed and self.guardian is None:
                await self.discover_guard()
            return
        if self.login_managed:
            await self.stop()
            raise RACPError(
                "SESSION_UNAVAILABLE", "waiting for the user's logon Broker", layer="agent"
            )
        await self.stop()
        if time.monotonic() < self.next_start:
            raise RACPError("SESSION_UNAVAILABLE", "Broker restart backoff", layer="agent")
        identity = process_identity(os.getpid())
        if identity.session != self.session_id:
            # Cross-session user launch is reserved for the SCM/WTS-token adapter.
            raise RACPError(
                "SESSION_UNAVAILABLE",
                "foreground Agent cannot launch another user's session",
                layer="agent",
            )
        config = BrokerConfig.pair(identity, identity, secrets.token_hex(16))
        config.job_name = "Global\\RACP-Broker-Job-" + config.pair_id
        self.config, self.path = config, config.save(self.root)
        try:
            self.process = await asyncio.create_subprocess_exec(
                getattr(sys, "_base_executable", sys.executable),
                "-I",
                str(Path(__file__).parents[1] / "providers/spawn_gate.py"),
                "--hidden",
                cwd=self.root,
                env=execution_env({}),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW, including the stdlib gate.
            )
            import win32api
            import win32con
            import win32job

            self.job = create_broker_job(config)
            handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, self.process.pid
            )
            try:
                win32job.AssignProcessToJobObject(self.job, handle)
            finally:
                handle.Close()
            assert self.process.stdin and self.process.stdout
            argv = self._command(self.path)
            self.process.stdin.write(json.dumps(argv).encode() + b"\n")
            await self.process.stdin.drain()
            self.process.stdin.close()
            reply = json.loads(await asyncio.wait_for(self.process.stdout.readline(), 5))
            if "pid" not in reply:
                raise RACPError(
                    "SESSION_UNAVAILABLE", "Broker process could not start", layer="agent"
                )
            self.status = await self.call("broker.status", {}, self.private_context(), 5)
            parent = process_identity(self.status["broker_pid"])
            if abs(parent.created - self.status["broker_created"]) > 0.000001:
                raise PermissionError("Broker status process identity changed")
            config.require_broker(parent)
            try:
                controller = Controller(
                    pid=identity.pid,
                    created=identity.created,
                    sid=identity.sid,
                    session=identity.session,
                )
                guarded = GuardConfig.pair_guard(parent, identity.sid, controller, config.job_name)
                spawning_guard = asyncio.create_task(asyncio.to_thread(start_guard, guarded))
                interrupted = False
                while True:
                    try:
                        self.guardian = await asyncio.shield(spawning_guard)
                        break
                    except asyncio.CancelledError:
                        if spawning_guard.cancelled():
                            raise
                        interrupted = True
                if interrupted:
                    raise asyncio.CancelledError
                await self.call(
                    "broker.guard_attach",
                    self.guardian.config.model_dump(),
                    self.private_context(),
                    5,
                )
            except (RACPError, OSError, NativeError, TimeoutError, PermissionError, ValueError):
                # Observation/UIA remain available when the local host disallows breakaway.
                # Win32 input refuses dispatch until an independent guardian is attached.
                if self.guardian is not None:
                    await asyncio.to_thread(self.guardian.close)
                    self.guardian = None
            self.status = await self.call("broker.status", {}, self.private_context(), 5)
            self.backoff = 0.5
            self.cleanup_status = "complete"
        except BaseException:
            self.next_start = time.monotonic() + self.backoff
            self.backoff = min(30, self.backoff * 2)
            await self.finish_stop()
            raise

    async def finish_stop(self) -> None:
        cleanup = asyncio.create_task(self.stop())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        await cleanup

    async def discover_guard(self) -> None:
        if self.guardian is not None or self.config is None:
            return
        information = await self.call("broker.guard_info", {}, self.private_context(), 5)
        if information.get("guardian") is None:
            return
        try:
            config = GuardConfig.model_validate(information["guardian"])
            parent = process_identity(config.agent_pid)
            self.config.require_broker(parent)
            if config.controller is None or config.job_name != self.config.job_name:
                raise PermissionError("registered input guardian scope mismatch")
            config.controller.require(process_identity(os.getpid()))
            if config.controller_principal != self.config.agent_acl_sid:
                raise PermissionError("registered input guardian controller ACL mismatch")
            peer = pin_guard(config)
        except (ValueError, PermissionError) as exc:
            raise RACPError(
                "PERMISSION_DENIED", "registered input guardian scope rejected", layer="agent"
            ) from exc
        self.guardian = GuardProcess(config, None, peer)

    async def call(
        self, operation: str, payload: dict[str, Any], context: dict[str, Any], io_seconds: float
    ) -> dict[str, Any]:
        if self.config is None:
            raise RACPError("SESSION_UNAVAILABLE", "Broker is not paired", layer="agent")
        response = await asyncio.to_thread(
            request,
            self.config,
            {
                "operation": operation,
                "payload": payload,
                "context": context,
            },
            io_seconds,
        )
        if response.get("state") != "SUCCEEDED":
            error = response.get("error") or {}
            raise RACPError(
                error.get("code", "BROKER_FAILURE"),
                error.get("message", "Broker failed"),
                layer="broker",
                execution_state=error.get("execution_state", "unknown"),
                **error.get("details", {}),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RACPError(
                "EXECUTION_UNKNOWN",
                "invalid Broker response",
                layer="agent",
                execution_state="unknown",
            )
        return result

    async def stop(self) -> None:
        process, job = self.process, self.job
        self.process, self.job = None, None
        if job is not None:
            import psutil
            import win32api
            import win32event
            import win32job

            handles = []
            if process is not None:
                try:
                    descendants = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    descendants = []
                for child in descendants:
                    try:
                        handle = win32api.OpenProcess(0x101000, False, child.pid)
                        if win32job.IsProcessInJob(handle, job):
                            handles.append(handle)
                        else:
                            handle.Close()
                    except (OSError, NativeError):
                        pass
            win32job.TerminateJobObject(job, 1)
            deadline = time.monotonic() + 5
            try:
                while (
                    win32job.QueryInformationJobObject(
                        job, win32job.JobObjectBasicAccountingInformation
                    )["ActiveProcesses"]
                    or any(
                        win32event.WaitForSingleObject(h, 0) == win32event.WAIT_TIMEOUT
                        for h in handles
                    )
                    or (
                        isinstance(process, PeerProcess)
                        and win32event.WaitForSingleObject(process.handle, 0)
                        == win32event.WAIT_TIMEOUT
                    )
                ):
                    if time.monotonic() >= deadline:
                        self.cleanup_status = "unknown"
                        break
                    await asyncio.sleep(0.01)
            finally:
                for handle in handles:
                    handle.Close()
                job.Close()
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), 5)
        if isinstance(process, PeerProcess):
            process.close()
        if self.guardian is not None:
            try:
                if not await asyncio.to_thread(self.guardian.close):
                    self.cleanup_status = "unknown"
            except (RACPError, OSError, NativeError, TimeoutError, PermissionError):
                self.cleanup_status = "unknown"
            finally:
                self.guardian = None
        if self.path is not None:
            self.path.unlink(missing_ok=True)
        self.path, self.config = None, None
        self.status = {"available": False, "reason": "Broker stopped"}

    async def abort(self) -> None:
        if self.process is None:
            return
        try:
            await self.call("broker.abort", {}, self.private_context(), 1)
        except (OSError, NativeError, RACPError, TimeoutError):
            pass
        await self.stop()

    async def rpc(
        self, operation: str, payload: dict[str, Any], context: dict[str, Any], deadline: float
    ) -> dict[str, Any]:
        async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
            async with self.lock:
                await self.start()
                # Cancellation cannot abandon a native thread executing input.
                work = asyncio.create_task(
                    self.call(operation, payload, context, max(0.001, deadline - time.monotonic()))
                )
                try:
                    return await asyncio.shield(work)
                except (asyncio.CancelledError, OSError, NativeError, TimeoutError):
                    cleanup = asyncio.create_task(self.abort())
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                    await cleanup
                    await asyncio.gather(work, return_exceptions=True)
                    self.next_start = time.monotonic() + self.backoff
                    self.backoff = min(30, self.backoff * 2)
                    if self.cleanup_status != "complete":
                        raise RACPError(
                            "EXECUTION_UNKNOWN",
                            "Broker cleanup unverified",
                            layer="agent",
                            execution_state="unknown",
                        ) from None
                    raise

    def protected_pids(self) -> set[int]:
        import psutil

        if self.process is None:
            return {self.guardian.peer.pid} if self.guardian is not None else set()
        try:
            return {
                self.process.pid,
                *({self.guardian.peer.pid} if self.guardian is not None else set()),
                *[p.pid for p in psutil.Process(self.process.pid).children(recursive=True)],
            }
        except psutil.NoSuchProcess:
            return set()
