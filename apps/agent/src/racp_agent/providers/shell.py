import asyncio
import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from racp_agent.providers.containment import kill_group
from racp_agent.workspaces import WorkspacePaths, WorkspaceSpec
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import ShellInput

PROTECTED_ENV = {
    "PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "COMSPEC",
    "SYSTEMROOT",
    "WINDIR",
}
WINDOWS_OS_ENV = {"SYSTEMDRIVE", "ALLUSERSPROFILE"}


def execution_env(overrides: Mapping[str, str | None]) -> dict[str, str]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
    }
    if os.name == "nt":
        # Native known-folder APIs expand registry paths such as
        # %SystemDrive%\\ProgramData in the child process environment.
        allowed.update(WINDOWS_OS_ENV)
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    if os.name == "nt" and not any(key.upper() == "SYSTEMDRIVE" for key in env):
        import win32api

        # The Agent itself can have been launched in a sanitized environment.
        # Query the OS rather than guessing C: or trusting a caller override.
        env["SystemDrive"] = os.path.splitdrive(win32api.GetWindowsDirectory())[0]
    for key, value in overrides.items():
        if (
            key.upper() in PROTECTED_ENV
            or (os.name == "nt" and key.upper() in WINDOWS_OS_ENV)
            or key.upper().startswith(("RACP_", "OPENAI_", "AWS_"))
        ):
            raise RACPError("PERMISSION_DENIED", "protected environment variable", layer="provider")
        if os.name == "nt":
            for previous in list(env):
                if previous.upper() == key.upper():
                    del env[previous]
        if value is not None:
            env[key] = value
    return env


class ShellProvider:
    def __init__(
        self, workspace: Path, spool: Path, additional: tuple[WorkspaceSpec, ...] = ()
    ) -> None:
        self.paths = WorkspacePaths(workspace, additional)
        self.workspace = workspace.resolve(strict=True)
        self.spool = spool
        spool.mkdir(parents=True, exist_ok=True)
        self.shells: dict[str, str] = {}
        self.output_limit_bytes = 64 * 1024 * 1024
        self.spool_file_limit_bytes = 72 * 1024 * 1024
        if os.name == "nt":
            system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
            self.shells = {
                "cmd": str(system / "System32/cmd.exe"),
                "powershell": str(system / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            }
        else:
            self.shells = {"bash": "/bin/bash"}

    def argv(self, payload: ShellInput) -> list[str]:
        if payload.mode == "argv":
            assert payload.argv is not None
            # Windows .bat/.cmd can invoke a shell implicitly even with shell=False.
            if os.name == "nt" and Path(payload.argv[0]).suffix.lower() in {".bat", ".cmd"}:
                raise RACPError(
                    "INVALID_ARGUMENT",
                    "batch commands require explicit shell mode",
                    layer="provider",
                )
            return payload.argv
        if payload.shell not in self.shells:
            raise RACPError("OPERATION_NOT_SUPPORTED", "shell is not configured", layer="provider")
        flags = {
            "cmd": ["/d", "/s", "/c"],
            "powershell": ["-NoProfile", "-NonInteractive", "-Command"],
            "pwsh": ["-NoProfile", "-NonInteractive", "-Command"],
            "bash": ["-c"],
        }
        assert payload.command is not None and payload.shell is not None
        return [self.shells[payload.shell], *flags[payload.shell], payload.command]

    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        data = ShellInput.model_validate(payload)
        target = self.argv(data)
        env = execution_env(data.env)
        cwd = self.paths.cwd(data.cwd, context.workspace_id)
        started = time.monotonic()
        job: Any = None
        stdout, stderr = bytearray(), bytearray()
        totals = [0, 0]
        output_path = self.spool / f"{context.operation_id}.output"
        saved_bytes = 0
        collected_bytes = 0
        resource_failure = asyncio.Event()
        resource_reason = "output_limit"
        process: asyncio.subprocess.Process | None = None
        reason = "exited"
        cleanup = "complete"
        try:
            output_file = output_path.open("wb", buffering=0)
        except OSError as exc:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "output spool cannot be created", layer="provider"
            ) from exc
        with output_file as output:

            async def drain(stream: asyncio.StreamReader, buffer: bytearray, index: int) -> None:
                nonlocal saved_bytes, collected_bytes, resource_reason
                while chunk := await stream.read(8192):
                    totals[index] += len(chunk)
                    if resource_failure.is_set():
                        # Keep consuming until containment has terminated the tree and EOF
                        # closes the pipes. An abandoned, paused StreamReader can otherwise
                        # prevent subprocess.wait() from completing after process exit.
                        continue
                    piece = chunk[: max(0, self.output_limit_bytes - collected_bytes)]
                    collected_bytes += len(piece)
                    keep = max(0, data.max_output_bytes // 2 - len(buffer))
                    buffer.extend(piece[:keep])
                    # Bounded spool stores framed raw bytes with explicit channel.
                    if piece:
                        frame = bytes([index]) + len(piece).to_bytes(4, "big") + piece
                        if output.tell() + len(frame) > self.spool_file_limit_bytes:
                            resource_reason = "spool_limit"
                            resource_failure.set()
                            continue
                        position = output.tell()
                        try:
                            if output.write(frame) != len(frame):
                                raise OSError("short spool write")
                        except OSError:
                            resource_reason = "spool_write_failed"
                            try:
                                output.truncate(position)
                            except OSError:
                                pass
                            resource_failure.set()
                            continue
                        saved_bytes += len(piece)
                    if len(piece) != len(chunk):
                        resource_failure.set()

            try:
                kwargs: dict[str, Any] = {}
                launch = target
                if os.name == "nt":
                    # A Windows venv python.exe is a redirector: it can spawn the real
                    # interpreter before Job assignment. The gate must be the base
                    # interpreter so nothing forks before it receives stdin permission.
                    launch = [
                        getattr(sys, "_base_executable", sys.executable),
                        "-I",
                        str(Path(__file__).with_name("launch.py")),
                    ]
                else:
                    kwargs["start_new_session"] = True
                process = await asyncio.create_subprocess_exec(
                    *launch,
                    cwd=cwd,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **kwargs,
                )
                if os.name == "nt":
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
                assert process.stdin and process.stdout and process.stderr
                if os.name == "nt":
                    process.stdin.write(json.dumps(target).encode() + b"\n")
                    await process.stdin.drain()
                process.stdin.close()
                readers = [
                    asyncio.create_task(drain(process.stdout, stdout, 0)),
                    asyncio.create_task(drain(process.stderr, stderr, 1)),
                ]
                try:
                    async with asyncio.timeout(context.timeout_ms / 1000):
                        exited = asyncio.create_task(process.wait())
                        exhausted = asyncio.create_task(resource_failure.wait())
                        try:
                            completed, _ = await asyncio.wait(
                                [exited, exhausted], return_when=asyncio.FIRST_COMPLETED
                            )
                            if exhausted in completed:
                                reason = resource_reason
                        finally:
                            for waiter in (exited, exhausted):
                                waiter.cancel()
                            await asyncio.gather(exited, exhausted, return_exceptions=True)
                except TimeoutError:
                    reason = "timeout"
                except asyncio.CancelledError:
                    reason = "cancelled"
                finally:
                    # Even a successful parent may leave descendants and inherited pipe handles.
                    if job is not None:
                        win32job.TerminateJobObject(job, 1)
                        job.Close()
                        job = None
                    elif os.name != "nt":
                        try:
                            kill_group(process.pid)
                        except ProcessLookupError:
                            pass
                    if process.returncode is None:
                        process.kill()

                    async def confirm_cleanup() -> None:
                        nonlocal cleanup
                        try:
                            async with asyncio.timeout(5):
                                await process.wait()
                                await asyncio.gather(*readers)
                        except (TimeoutError, OSError):
                            cleanup = "partial"
                            for task in readers:
                                task.cancel()
                            await asyncio.gather(*readers, return_exceptions=True)

                    confirmation = asyncio.create_task(confirm_cleanup())
                    try:
                        await asyncio.shield(confirmation)
                    except asyncio.CancelledError:
                        await asyncio.shield(confirmation)
                    if resource_failure.is_set():
                        reason = resource_reason
            except OSError as exc:
                raise RACPError(
                    "PATH_NOT_FOUND", "executable could not be started", layer="provider"
                ) from exc
            finally:
                if job is not None:
                    win32job.TerminateJobObject(job, 1)
                    job.Close()
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
            flush = asyncio.create_task(asyncio.to_thread(os.fsync, output.fileno()))
            try:
                try:
                    await asyncio.shield(flush)
                except asyncio.CancelledError:
                    await asyncio.shield(flush)
            except OSError:
                reason = "spool_write_failed"
        decoded = [
            stdout.decode(data.encoding, errors="replace"),
            stderr.decode(data.encoding, errors="replace"),
        ]
        inline = [
            text.encode("utf-8")[: data.max_output_bytes // 2].decode("utf-8", errors="ignore")
            for text in decoded
        ]
        truncated = totals[0] > len(stdout) or totals[1] > len(stderr) or inline != decoded
        result = {
            "operation_id": context.operation_id,
            "exit_code": process.returncode if process and reason == "exited" else None,
            "stdout": inline[0],
            "stderr": inline[1],
            "duration_ms": int((time.monotonic() - started) * 1000),
            "truncated": truncated,
            "artifact_id": None,
            "termination_reason": reason,
            "cleanup_status": cleanup,
            "decoding_errors": sum(text.count("\ufffd") for text in decoded),
            "stdout_bytes": totals[0],
            "stderr_bytes": totals[1],
            "artifact_truncated": saved_bytes < sum(totals),
            "collected_bytes": collected_bytes,
            "spooled_bytes": saved_bytes,
            "output_limit_bytes": self.output_limit_bytes,
        }
        if truncated:
            result["spool_path"] = str(output_path)
        else:
            output_path.unlink(missing_ok=True)
        if reason != "exited":
            resource_exhausted = reason in {"output_limit", "spool_limit", "spool_write_failed"}
            error = RACPError(
                "RESOURCE_EXHAUSTED"
                if resource_exhausted
                else "TIMEOUT"
                if reason == "timeout"
                else "CANCELLED",
                f"execution {reason}",
                layer="provider",
                execution_state="completed",
                partial_result=result,
            )
            return {
                "state": "FAILED"
                if resource_exhausted
                else "TIMED_OUT"
                if reason == "timeout"
                else "CANCELLED",
                "result": result,
                "error": asdict(error.error),
            }
        return {"state": "SUCCEEDED", "result": result, "error": None}
