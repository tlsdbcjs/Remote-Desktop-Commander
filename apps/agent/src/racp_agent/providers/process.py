import asyncio
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil
from racp_agent.providers.containment import kill_group
from racp_agent.providers.shell import ShellProvider, execution_env
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import ShellInput, new_id, timestamp
from racp_sdk.pagination import CursorCodec


@dataclass
class ManagedProcess:
    handle_id: str
    principal_id: str
    boot_id: str
    pid: int
    create_time: float
    process: asyncio.subprocess.Process
    job: Any
    created_at: str
    expires_at: str
    expires_monotonic: float
    closed: bool = False
    workspace_id: str = "default"

    def handle(self, device_id: str, provider_id: str) -> dict[str, Any]:
        return {
            "id": self.handle_id,
            "type": "interactive-process",
            "device_id": device_id,
            "owner": self.principal_id,
            "agent_boot_id": self.boot_id,
            "workspace_id": self.workspace_id,
            "provider_instance_id": provider_id,
            "resource_revision": "2" if self.closed or self.process.returncode is not None else "1",
            "created_at": self.created_at,
            "last_access_at": self.created_at,
            "expires_at": self.expires_at,
            "state": "CLOSED"
            if self.closed
            else "CLOSED"
            if self.process.returncode is not None
            else "ACTIVE",
            "availability": "available" if self.process.returncode is None else "exited",
            "pid": self.pid,
            "create_time": self.create_time,
            "exit_code": self.process.returncode,
            "output": "discard",
        }


class ProcessProvider:
    def __init__(self, shell: ShellProvider) -> None:
        self.shell = shell
        self.instance_id = new_id("provider")
        self.managed: dict[str, ManagedProcess] = {}
        self.cursor = CursorCodec()
        self.snapshots: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self.snapshot_lock = threading.RLock()
        self.extra_protected_pids: Callable[[], set[int]] = lambda: set()

    @staticmethod
    def describe(process: psutil.Process, boot_id: str) -> dict[str, Any]:
        info = process.as_dict(
            attrs=["pid", "ppid", "name", "exe", "cmdline", "username", "status", "create_time"],
            ad_value=None,
        )
        info["agent_boot_id"] = boot_id
        return info

    def identity(self, payload: dict[str, Any], context: ExecutionContext) -> psutil.Process:
        if payload.get("agent_boot_id", context.agent_boot_id) != context.agent_boot_id:
            raise RACPError("PRECONDITION_FAILED", "Agent boot identity changed", layer="provider")
        try:
            process = psutil.Process(payload["pid"])
            if (
                payload.get("create_time") is not None
                and abs(process.create_time() - payload["create_time"]) > 0.000001
            ):
                raise RACPError("PRECONDITION_FAILED", "PID identity changed", layer="provider")
            return process
        except psutil.NoSuchProcess as exc:
            raise RACPError(
                "PROCESS_NOT_FOUND", "process no longer exists", layer="provider"
            ) from exc

    def list_processes(self, payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        now = time.monotonic()
        self.snapshots = {key: value for key, value in self.snapshots.items() if value[0] > now}
        if payload["cursor"]:
            # Cursor revision is the snapshot ID; try the bounded live snapshots.
            snapshot_id, offset = "", 0
            for key in self.snapshots:
                try:
                    offset = self.cursor.decode(
                        payload["cursor"], "process.list:" + str(payload["limit"]), key
                    )
                    snapshot_id = key
                    break
                except RACPError:
                    continue
            if not snapshot_id:
                raise RACPError(
                    "CURSOR_EXPIRED", "process snapshot cursor expired", layer="provider"
                )
        else:
            if len(self.snapshots) >= 16:
                self.snapshots.pop(next(iter(self.snapshots)))
            snapshot_id = new_id("snapshot")
            items = []
            for process in psutil.process_iter():
                try:
                    items.append(self.describe(process, context.agent_boot_id))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            items.sort(key=lambda item: item["pid"])
            self.snapshots[snapshot_id] = (now + 300, items)
            offset = 0
        items = self.snapshots[snapshot_id][1]
        end = offset + payload["limit"]
        return {
            "items": items[offset:end],
            "next_cursor": self.cursor.encode(
                end, "process.list:" + str(payload["limit"]), snapshot_id
            )
            if end < len(items)
            else None,
            "consistency": "snapshot_observation",
            "snapshot_id": snapshot_id,
        }

    def read_operation(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        if operation == "process.list":
            with self.snapshot_lock:
                return self.list_processes(payload, context)
        process = self.identity(payload, context)
        try:
            if operation == "process.inspect":
                result = self.describe(process, context.agent_boot_id)
                managed = next(
                    (
                        item
                        for item in self.managed.values()
                        if item.pid == process.pid and item.create_time == process.create_time()
                    ),
                    None,
                )
                if managed and managed.principal_id == context.principal_id:
                    result["handle"] = managed.handle(context.device_id, self.instance_id)
                return result
            children = process.children(recursive=True)
            if len(children) > 1000:
                raise RACPError(
                    "RESOURCE_EXHAUSTED", "process tree exceeds 1000 entries", layer="provider"
                )
            tree = [self.describe(process, context.agent_boot_id)]
            for child in children:
                try:
                    tree.append(self.describe(child, context.agent_boot_id))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return {"items": tree}
        except psutil.AccessDenied as exc:
            raise RACPError(
                "PERMISSION_DENIED", "process information access denied", layer="provider"
            ) from exc

    async def _spawn(self, payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        active = sum(item.process.returncode is None for item in self.managed.values())
        if active >= 32:
            raise RACPError("RESOURCE_EXHAUSTED", "managed process limit reached", layer="provider")
        if len(self.managed) >= 64:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "managed process history limit reached", layer="provider"
            )
        data = ShellInput.model_validate(
            {key: value for key, value in payload.items() if key != "output"}
        )
        argv = self.shell.argv(data)
        cwd = self.shell.paths.cwd(data.cwd, context.workspace_id)
        job: Any = None
        process: asyncio.subprocess.Process | None = None
        try:
            if os.name == "nt":
                launch = [
                    getattr(sys, "_base_executable", sys.executable),
                    "-I",
                    str(Path(__file__).with_name("spawn_gate.py")),
                ]
                process = await asyncio.create_subprocess_exec(
                    *launch,
                    cwd=cwd,
                    env=execution_env(data.env),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                import win32api
                import win32con
                import win32job

                job = win32job.CreateJobObject(None, "")
                info = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation
                )
                info["BasicLimitInformation"]["LimitFlags"] = (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                )
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, info
                )
                handle = win32api.OpenProcess(
                    win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid
                )
                try:
                    win32job.AssignProcessToJobObject(job, handle)
                finally:
                    handle.Close()
                assert process.stdin and process.stdout
                process.stdin.write(json.dumps(argv).encode() + b"\n")
                await process.stdin.drain()
                process.stdin.close()
                reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
                if "pid" not in reply:
                    raise RACPError(
                        "PATH_NOT_FOUND", "executable could not be started", layer="provider"
                    )
                pid = int(reply["pid"])
            else:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=cwd,
                    env=execution_env(data.env),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                pid = process.pid
            try:
                create_time = psutil.Process(pid).create_time()
            except psutil.NoSuchProcess:
                create_time = 0.0
            managed = ManagedProcess(
                new_id("proc"),
                context.principal_id,
                context.agent_boot_id,
                pid,
                create_time,
                process,
                job,
                timestamp(),
                (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                time.monotonic() + 3600,
                workspace_id=context.workspace_id,
            )
            self.managed[managed.handle_id] = managed
            return {
                "handle": managed.handle(context.device_id, self.instance_id),
                "pid": pid,
                "create_time": create_time,
                "agent_boot_id": context.agent_boot_id,
            }
        except BaseException:
            if job is not None:
                win32job.TerminateJobObject(job, 1)
                job.Close()
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            raise

    async def close(self, managed: ManagedProcess) -> None:
        if managed.closed:
            return
        cleanup_deadline = time.monotonic() + 5
        if managed.job is not None:
            import win32job

            win32job.TerminateJobObject(managed.job, 1)
            # Job termination is asynchronous. The spawn gate can exit before
            # the actual target/descendants, so its wait alone is insufficient.
            while win32job.QueryInformationJobObject(
                managed.job, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]:
                if time.monotonic() >= cleanup_deadline:
                    raise TimeoutError("owned process tree cleanup did not complete")
                await asyncio.sleep(0.01)
            managed.job.Close()
            managed.job = None
        elif os.name != "nt":
            try:
                kill_group(managed.process.pid)
            except ProcessLookupError:
                pass
        if managed.process.returncode is None:
            try:
                managed.process.kill()
            except ProcessLookupError:
                pass
        await asyncio.wait_for(
            managed.process.wait(), max(0.001, cleanup_deadline - time.monotonic())
        )
        managed.closed = True

    async def cleanup(self, *, expired_only: bool = False) -> None:
        for managed in list(self.managed.values()):
            if not expired_only or managed.expires_monotonic <= time.monotonic():
                await self.close(managed)
                if expired_only:
                    self.managed.pop(managed.handle_id, None)

    async def wait(self, payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        process = self.identity(payload, context)
        deadline = time.monotonic() + context.timeout_ms / 1000
        while process.is_running():
            self.identity(payload, context)
            if time.monotonic() >= deadline:
                raise RACPError(
                    "TIMEOUT",
                    "process wait timed out; target remains running",
                    layer="provider",
                    target_terminated=False,
                )
            await asyncio.sleep(0.05)
        managed = next(
            (
                item
                for item in self.managed.values()
                if item.pid == payload["pid"] and item.create_time == payload["create_time"]
            ),
            None,
        )
        if managed:
            await managed.process.wait()
        return {
            "pid": payload["pid"],
            "exited": True,
            "exit_code": managed.process.returncode if managed else None,
        }

    async def terminate(self, payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        process = self.identity(payload, context)
        protected = {0, 1, os.getpid(), *[parent.pid for parent in psutil.Process().parents()]}
        protected.update(self.extra_protected_pids())
        try:
            command = process.cmdline()
        except psutil.AccessDenied:
            command = []
        protected_names = {
            "system",
            "registry",
            "smss.exe",
            "csrss.exe",
            "wininit.exe",
            "services.exe",
            "lsass.exe",
            "winlogon.exe",
        }
        if (
            process.pid in protected
            or process.name().casefold() in protected_names
            or any(
                any(
                    name in argument
                    for name in (
                        "racp_gateway",
                        "racp-gateway",
                        "racp_agent.",
                        "racp-agent",
                        "racp_session_broker",
                        "racp-session-broker",
                        "racp-login-broker",
                    )
                )
                for argument in command
            )
        ):
            raise RACPError(
                "PERMISSION_DENIED", "protected process cannot be terminated", layer="provider"
            )
        managed = next(
            (
                item
                for item in self.managed.values()
                if item.pid == process.pid and item.create_time == payload["create_time"]
            ),
            None,
        )
        if managed and managed.principal_id != context.principal_id:
            raise RACPError(
                "PERMISSION_DENIED", "process belongs to another principal", layer="provider"
            )
        if payload["force"] and managed:
            await self.close(managed)
            return {
                "pid": process.pid,
                "exited": True,
                "cleanup_status": "complete",
                "method": "owned_tree_kill",
            }
        try:
            if payload["force"]:
                process.kill()
            elif os.name == "nt":
                # Windows has no generic graceful equivalent to SIGTERM. Console targets
                # spawned by this provider have their own group; GUI targets receive WM_CLOSE.
                import win32api
                import win32gui
                import win32process

                windows: list[int] = []

                def find_window(window: int, parameter: Any) -> None:
                    if win32process.GetWindowThreadProcessId(window)[1] == process.pid:
                        windows.append(window)

                win32gui.EnumWindows(find_window, None)
                if windows:
                    for window in windows:
                        win32gui.PostMessage(window, 0x0010, 0, 0)
                    return {
                        "pid": process.pid,
                        "exited": False,
                        "signal_sent": True,
                        "method": "WM_CLOSE",
                        "next_action": "process_wait",
                    }

                try:
                    win32api.GenerateConsoleCtrlEvent(1, process.pid)
                except Exception as exc:
                    raise RACPError(
                        "OPERATION_NOT_SUPPORTED",
                        "graceful console signal unavailable; force requires explicit opt-in",
                        layer="provider",
                    ) from exc
            else:
                process.terminate()
        except psutil.AccessDenied as exc:
            raise RACPError(
                "PERMISSION_DENIED", "process termination access denied", layer="provider"
            ) from exc
        return {
            "pid": process.pid,
            "exited": False,
            "signal_sent": True,
            "force": payload["force"],
            "next_action": "process_wait",
        }

    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        try:
            if operation in {"process.memory_regions", "process.memory_read"}:
                from racp_agent.providers.process_memory import execute as memory_execute

                self.identity(payload, context)
                protected = {
                    os.getpid(),
                    *[p.pid for p in psutil.Process().parents()],
                    *self.extra_protected_pids(),
                }
                if payload["pid"] in protected:
                    raise RACPError(
                        "PERMISSION_DENIED",
                        "Agent/control process memory is protected",
                        layer="provider",
                    )
                return await memory_execute(operation, payload, context, self.shell.spool)
            if operation == "process.spawn":
                task = asyncio.create_task(self._spawn(payload, context))
                try:
                    result = await asyncio.shield(task)
                except asyncio.CancelledError:
                    result = await asyncio.shield(task)
                    await self.close(self.managed[result["handle"]["id"]])
                    raise RACPError(
                        "CANCELLED", "spawn cancelled and owned tree cleaned", layer="provider"
                    ) from None
            elif operation == "process.terminate":
                result = await self.terminate(payload, context)
            elif operation == "process.wait":
                result = await self.wait(payload, context)
            else:
                result = await asyncio.to_thread(self.read_operation, operation, payload, context)
            return {"state": "SUCCEEDED", "result": result, "error": None}
        except (psutil.NoSuchProcess, ProcessLookupError) as exc:
            raise RACPError(
                "PROCESS_EXITED", "process exited during observation", layer="provider"
            ) from exc
