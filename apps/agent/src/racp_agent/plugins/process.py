import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from racp_domain.models import RACPError
from racp_protocol.models import MAX_MESSAGE_BYTES

from racp_agent.providers.containment import kill_group
from racp_agent.providers.shell import execution_env


class ProcessCommand(Protocol):
    command: list[str]
    working_directory: str
    environment: dict[str, str]
    max_memory_bytes: int


@dataclass
class ContainedCommand:
    """Fixed Agent recipes may reuse process containment without a plugin manifest."""

    command: list[str]
    working_directory: str
    environment: dict[str, str] = field(default_factory=dict)
    max_memory_bytes: int = 256 * 1024**2


class OwnedPluginProcess:
    def __init__(self, process: asyncio.subprocess.Process, job: Any) -> None:
        self.process, self.job = process, job
        self.cleanup_status = "pending"
        self.pinned: dict[int, Any] = {}
        self.pin_failed = False

    @classmethod
    async def start(cls, manifest: ProcessCommand) -> "OwnedPluginProcess":
        spawned = asyncio.create_task(cls._spawn(manifest))
        cancelled = False
        while not spawned.done():
            try:
                await asyncio.shield(spawned)
            except asyncio.CancelledError:
                cancelled = True
        owned = await spawned
        if cancelled:
            await owned.stop()
            if owned.cleanup_status != "complete":
                raise RACPError(
                    "EXECUTION_UNKNOWN",
                    "plugin spawn cancellation cleanup unverified",
                    layer="plugin",
                    execution_state="unknown",
                    cleanup_status="unverified",
                )
            raise asyncio.CancelledError
        return owned

    @classmethod
    async def _spawn(cls, manifest: ProcessCommand) -> "OwnedPluginProcess":
        process = None
        job: Any = None
        argv = manifest.command
        kwargs: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {}
        bootstrap = (json.dumps(argv, ensure_ascii=False) + "\n").encode()
        if len(bootstrap) > 256 * 1024:
            raise ValueError("plugin command exceeds gate limit")
        if os.name == "nt":
            import win32job

            job = win32job.CreateJobObject(None, "")
            try:
                info = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation
                )
                info["BasicLimitInformation"]["LimitFlags"] = (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
                    | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                )
                info["BasicLimitInformation"]["ActiveProcessLimit"] = 64
                info["JobMemoryLimit"] = manifest.max_memory_bytes
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, info
                )
            except BaseException:
                job.Close()
                raise
            argv = [
                getattr(sys, "_base_executable", sys.executable),
                "-I",
                str(Path(__file__).with_name("spawn_gate.py")),
            ]
            kwargs["creationflags"] = 0x08000200  # NO_WINDOW | NEW_PROCESS_GROUP
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=manifest.working_directory,
                env=execution_env(manifest.environment),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_MESSAGE_BYTES,
                **kwargs,
            )
            if os.name == "nt":
                import win32api
                import win32job

                handle = win32api.OpenProcess(0x101, False, process.pid)
                try:
                    win32job.AssignProcessToJobObject(job, handle)
                finally:
                    handle.Close()
                assert process.stdin is not None
                process.stdin.write(bootstrap)
                await process.stdin.drain()
            return cls(process, job)
        except BaseException as exc:
            if process is not None:
                owned = cls(process, job)
                await owned.stop()
                if owned.cleanup_status != "complete":
                    raise RACPError(
                        "EXECUTION_UNKNOWN",
                        "plugin startup cleanup unverified",
                        layer="plugin",
                        execution_state="unknown",
                        cleanup_status="unverified",
                    ) from exc
            elif job is not None:
                job.Close()
            raise

    def pin_members(self) -> None:
        import pywintypes
        import win32api
        import win32job

        for pid in win32job.QueryInformationJobObject(
            self.job, win32job.JobObjectBasicProcessIdList
        ):
            if pid in self.pinned:
                continue
            try:
                handle = win32api.OpenProcess(0x101000, False, pid)
            except pywintypes.error as exc:
                if exc.winerror != 87:
                    self.pin_failed = True
                continue
            try:
                if win32job.IsProcessInJob(handle, self.job):
                    self.pinned[pid] = handle
                else:
                    handle.Close()
            except BaseException:
                handle.Close()
                raise

    def terminate(self) -> None:
        if self.job is not None:
            import win32job

            try:
                self.pin_members()
            except Exception:
                self.pin_failed = True
            win32job.TerminateJobObject(self.job, 1)
        elif os.name != "nt":
            try:
                kill_group(self.process.pid)
            except ProcessLookupError:
                pass
        if self.process.returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        async def drain(stream: asyncio.StreamReader | None) -> None:
            if stream is not None:
                while await stream.read(65536):
                    pass

        drains = [asyncio.create_task(drain(s)) for s in (self.process.stdout, self.process.stderr)]
        try:
            self.terminate()
            async with asyncio.timeout(5):
                await self.process.wait()
                if self.job is not None:
                    import win32event
                    import win32job

                    # Native Job accounting has no asyncio Event. This bounded poll
                    # confirms every descendant, rather than only the root process.
                    while True:
                        active = win32job.QueryInformationJobObject(
                            self.job, win32job.JobObjectBasicAccountingInformation
                        )["ActiveProcesses"]
                        pending = any(
                            win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT
                            for handle in self.pinned.values()
                        )
                        if not active and not pending:
                            break
                        await asyncio.sleep(0.01)
                await asyncio.gather(*drains)
            self.cleanup_status = "unverified" if self.pin_failed else "complete"
        except Exception:
            self.cleanup_status = "unverified"
        finally:
            for task in drains:
                task.cancel()
            await asyncio.gather(*drains, return_exceptions=True)
            if self.process.stdin is not None:
                self.process.stdin.close()
            if self.job is not None:
                self.job.Close()
                self.job = None
            for handle in self.pinned.values():
                handle.Close()
            self.pinned.clear()

    def running(self) -> bool:
        return self.process.returncode is None
