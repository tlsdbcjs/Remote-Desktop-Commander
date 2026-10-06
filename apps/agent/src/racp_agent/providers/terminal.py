import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from racp_agent.providers.shell import ShellProvider, execution_env
from racp_agent.providers.terminal_backend import TerminalBackend
from racp_agent.providers.terminal_buffer import TerminalBuffer
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import new_id, timestamp
from racp_protocol.terminal import TerminalOpen, TerminalWrite


@dataclass
class TerminalSession:
    id: str
    context: ExecutionContext
    backend: TerminalBackend
    cols: int
    rows: int
    buffer: TerminalBuffer = field(default_factory=TerminalBuffer)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task[None] | None = None
    created_at: str = field(default_factory=timestamp)
    last_access_at: str = field(default_factory=timestamp)
    expires_at: str = ""
    expires: float = 0
    state: str = "OPEN"
    eof: bool = False
    process_exit: int | None = None
    revision: int = 1
    cleanup_status: str = "complete"
    close_result: dict[str, Any] | None = None

    def touch(self) -> None:
        self.last_access_at = timestamp()
        self.expires_at = (
            (datetime.now(UTC) + timedelta(hours=8)).isoformat().replace("+00:00", "Z")
        )
        self.expires = time.monotonic() + 8 * 3600


class TerminalProvider:
    def __init__(self, shell: ShellProvider) -> None:
        self.shell = shell
        self.instance_id = new_id("provider")
        self.sessions: dict[str, TerminalSession] = {}
        self.open_lock = asyncio.Lock()

    def handle(self, session: TerminalSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "type": "terminal",
            "device_id": session.context.device_id,
            "owner": session.context.principal_id,
            "agent_boot_id": session.context.agent_boot_id,
            "workspace_id": session.context.workspace_id,
            "provider_instance_id": self.instance_id,
            "resource_revision": str(session.revision),
            "created_at": session.created_at,
            "last_access_at": session.last_access_at,
            "expires_at": session.expires_at,
            "state": "ACTIVE"
            if session.state == "OPEN"
            else "CLOSED"
            if session.state == "ENDED"
            else session.state,
            "availability": "available" if session.state == "OPEN" else "unavailable",
        }

    def identity(self, payload: dict[str, Any], context: ExecutionContext) -> TerminalSession:
        session = self.sessions.get(payload["handle_id"])
        if session is None or payload.get("agent_boot_id", context.agent_boot_id) not in {
            None,
            context.agent_boot_id,
        }:
            raise RACPError(
                "HANDLE_EXPIRED", "terminal belongs to a previous Agent", layer="provider"
            )
        if session.context.principal_id != context.principal_id:
            raise RACPError(
                "PERMISSION_DENIED", "terminal belongs to another owner", layer="provider"
            )
        if session.context.agent_boot_id != context.agent_boot_id:
            raise RACPError("HANDLE_EXPIRED", "Agent boot changed", layer="provider")
        return session

    async def pump(self, session: TerminalSession) -> None:
        teardown: asyncio.Task[None] | None = None
        try:
            while True:
                block = await asyncio.to_thread(session.backend.read)
                if block == b"":
                    break
                if block:
                    session.buffer.append(block)
                    session.changed.set()
                code = await asyncio.to_thread(session.backend.poll)
                if code is not None and teardown is None:
                    session.process_exit = code
                    # ConPTY teardown may emit a last frame. Keep reading while a
                    # separate thread closes the pseudoconsole to avoid deadlock.
                    teardown = asyncio.create_task(asyncio.to_thread(session.backend.finish))
                if not block:
                    await asyncio.sleep(0.01)
            if teardown is None:
                teardown = asyncio.create_task(asyncio.to_thread(session.backend.finish))
            await teardown
            session.process_exit = await asyncio.to_thread(session.backend.poll)
        except OSError:
            session.cleanup_status = "unknown"
            await asyncio.to_thread(session.backend.terminate)
            if teardown is not None:
                await teardown
        finally:
            await asyncio.to_thread(session.backend.close)
            session.eof = True
            if session.state == "OPEN":
                session.state = "ENDED"
                session.revision += 1
            session.changed.set()

    async def open(self, payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        data = TerminalOpen.model_validate(payload)
        async with self.open_lock:
            await self.cleanup(expired_only=True)
            if sum(item.state == "OPEN" for item in self.sessions.values()) >= 8:
                raise RACPError(
                    "RESOURCE_EXHAUSTED", "terminal session limit reached", layer="provider"
                )
            if len(self.sessions) >= 64:
                raise RACPError(
                    "RESOURCE_EXHAUSTED", "terminal history limit reached", layer="provider"
                )
            shell = data.shell or ("powershell" if os.name == "nt" else "bash")
            if data.argv is not None:
                argv = data.argv
            else:
                if shell not in self.shell.shells:
                    raise RACPError(
                        "CAPABILITY_UNAVAILABLE", "terminal shell unavailable", layer="provider"
                    )
                flags = {
                    "powershell": ["-NoLogo", "-NoProfile"],
                    "cmd": ["/d"],
                    "bash": ["--noprofile", "--norc", "-i"],
                }
                argv = [self.shell.shells[shell], *flags[shell]]
            env = execution_env(data.env)
            env.setdefault("TERM", "xterm-256color")
            executable = shutil.which(argv[0], path=env.get("PATH", os.defpath))
            if executable is None:
                raise RACPError("PATH_NOT_FOUND", "terminal executable not found", layer="provider")
            if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
                raise RACPError(
                    "INVALID_ARGUMENT", "terminal argv cannot launch a batch file", layer="provider"
                )
            argv = [executable, *argv[1:]]
            cwd = self.shell.paths.cwd(data.cwd, context.workspace_id)

            def start() -> TerminalBackend:
                if os.name == "nt":
                    from racp_agent.providers.terminal_windows import WindowsTerminal

                    return WindowsTerminal(argv, str(cwd), env, data.cols, data.rows)
                from racp_agent.providers.terminal_posix import PosixTerminal

                return PosixTerminal(argv, str(cwd), env, data.cols, data.rows)

            work = asyncio.create_task(asyncio.to_thread(start))
            cancelled = False
            try:
                backend = await asyncio.shield(work)
            except asyncio.CancelledError:
                backend = await asyncio.shield(work)
                cancelled = True
            session = TerminalSession(new_id("term"), context, backend, data.cols, data.rows)
            session.touch()
            self.sessions[session.id] = session
            session.task = asyncio.create_task(self.pump(session))
            if cancelled:
                await self.close(session)
                raise asyncio.CancelledError
            return {
                "handle_id": session.id,
                "handle": self.handle(session),
                "pid": backend.pid,
                "cols": data.cols,
                "rows": data.rows,
                "cursor": "0",
                "streams": "merged",
            }

    async def close(self, session: TerminalSession, *, expired: bool = False) -> dict[str, Any]:
        work = asyncio.create_task(self._close(session, expired=expired))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            return await asyncio.shield(work)

    async def _close(self, session: TerminalSession, *, expired: bool = False) -> dict[str, Any]:
        if session.close_result is not None:
            return session.close_result
        if not session.eof:
            await asyncio.to_thread(session.backend.terminate)
            assert session.task is not None
            await asyncio.shield(session.task)
        session.state = "EXPIRED" if expired else "CLOSED"
        session.revision += 1
        session.close_result = {
            "handle_id": session.id,
            "state": session.state,
            "process_exit": session.process_exit,
            "cleanup_status": session.cleanup_status,
            "handle": self.handle(session),
        }
        return session.close_result

    async def cleanup(self, *, expired_only: bool = False) -> None:
        for session in list(self.sessions.values()):
            if expired_only and time.monotonic() < session.expires:
                continue
            async with session.lock:
                await self.close(session, expired=True)
            if expired_only and time.monotonic() >= session.expires:
                self.sessions.pop(session.id, None)

    async def read(self, session: TerminalSession, payload: dict[str, Any]) -> dict[str, Any]:
        cursor = int(payload["cursor"])
        result = session.buffer.read(cursor, payload["max_bytes"], eof=session.eof)
        if result["next_cursor"] == str(cursor) and not session.eof and payload["wait_ms"]:
            session.changed.clear()
            try:
                await asyncio.wait_for(session.changed.wait(), payload["wait_ms"] / 1000)
            except TimeoutError:
                pass
            result = session.buffer.read(cursor, payload["max_bytes"], eof=session.eof)
        return {
            **result,
            "handle_id": session.id,
            "process_exit": session.process_exit,
            "handle": self.handle(session),
        }

    async def write(
        self,
        session: TerminalSession,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        raw = TerminalWrite.model_validate(payload).bytes()
        accepted = 0

        def send() -> None:
            nonlocal accepted
            while accepted < len(raw):
                written = session.backend.write(raw[accepted : accepted + 1024])
                if written <= 0:
                    raise OSError("terminal did not accept input")
                accepted += written

        work = asyncio.create_task(asyncio.to_thread(send))
        try:
            async with asyncio.timeout(context.timeout_ms / 1000):
                await asyncio.shield(work)
        except (TimeoutError, asyncio.CancelledError) as exc:
            await self.close(session)
            await asyncio.gather(work, return_exceptions=True)
            raise RACPError(
                "TIMEOUT" if isinstance(exc, TimeoutError) else "CANCELLED",
                "terminal input interrupted; do not replay as a new operation",
                layer="provider",
                execution_state="completed",
                accepted_bytes=accepted,
                accepted_bytes_exact=False,
                cleanup_status=session.cleanup_status,
                handle_id=session.id,
            ) from exc
        except OSError as exc:
            raise RACPError(
                "EXECUTION_UNKNOWN",
                "terminal input failed; inspect before new input",
                layer="provider",
                execution_state="unknown",
                accepted_bytes=accepted,
                accepted_bytes_exact=False,
                handle_id=session.id,
            ) from exc
        session.touch()
        return {"handle_id": session.id, "accepted_bytes": accepted, "accepted_bytes_exact": True}

    async def execute(
        self,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        budget = asyncio.timeout(context.timeout_ms / 1000)
        try:
            async with budget:
                return await self._execute(operation, payload, context)
        except TimeoutError as exc:
            raise RACPError(
                "TIMEOUT", "terminal operation budget elapsed", layer="provider"
            ) from exc
        except RACPError as exc:
            if budget.expired() and exc.error.code == "CANCELLED":
                raise RACPError(
                    "TIMEOUT",
                    "terminal input budget elapsed",
                    layer="provider",
                    execution_state=exc.error.execution_state,
                    **exc.error.details,
                ) from exc
            raise

    async def _execute(
        self,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        try:
            if operation == "terminal.open":
                result = await self.open(payload, context)
            else:
                session = self.identity(payload, context)
                if session.state == "EXPIRED" or time.monotonic() >= session.expires:
                    async with session.lock:
                        await self.close(session, expired=True)
                    raise RACPError("HANDLE_EXPIRED", "terminal TTL expired", layer="provider")
                if operation == "terminal.read":
                    result = await self.read(session, payload)
                else:
                    async with session.lock:
                        if operation == "terminal.close":
                            result = await self.close(session)
                        else:
                            if session.state != "OPEN":
                                raise RACPError(
                                    "HANDLE_EXPIRED", "terminal is closed", layer="provider"
                                )
                            if operation == "terminal.write":
                                result = await self.write(session, payload, context)
                            else:
                                if operation == "terminal.resize":
                                    await asyncio.to_thread(
                                        session.backend.resize, payload["cols"], payload["rows"]
                                    )
                                    session.cols, session.rows = payload["cols"], payload["rows"]
                                    session.revision += 1
                                session.touch()
                                result = {
                                    "handle_id": session.id,
                                    "cols": session.cols,
                                    "rows": session.rows,
                                    "handle": self.handle(session),
                                }
            return {"state": "SUCCEEDED", "result": result}
        except OSError as exc:
            raise RACPError(
                "INTERNAL_ERROR", "terminal transport failed", layer="provider"
            ) from exc
