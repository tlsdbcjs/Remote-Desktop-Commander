import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
from racp_domain.models import RACPError

from racp_agent.plugins.gdb_mi import MIRecord, parse_record
from racp_agent.providers.shell import execution_env


class GDBDriver:
    """One MI inferior, inherited plugin containment, fixed commands and bounded records."""

    def __init__(
        self, process: asyncio.subprocess.Process, on_state: Callable[[dict[str, Any]], None]
    ) -> None:
        self.process, self.on_state = process, on_state
        self.serial = asyncio.Lock()
        self.pending: dict[str, asyncio.Future[MIRecord]] = {}
        self.token, self.sequence = 0, 0
        self.state, self.reason, self.address = "STARTING", "initializing", None
        self.pid: int | None = None
        self.birth: float | None = None
        self.changed = asyncio.Event()
        self.log_bytes = 0
        self.utf8 = False
        self.readers = [asyncio.create_task(self.stdout()), asyncio.create_task(self.stderr())]

    @classmethod
    async def start(
        cls, executable: Path, directory: Path, on_state: Callable[[dict[str, Any]], None]
    ) -> "GDBDriver":
        process = await asyncio.create_subprocess_exec(
            str(executable),
            "-nx",
            "-nh",
            "--interpreter=mi2",
            "-q",
            cwd=directory,
            env=execution_env({}),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        )
        driver = cls(process, on_state)
        try:
            for command in (
                "-gdb-set auto-load off",
                "-gdb-set confirm off",
                "-gdb-set pagination off",
                "-gdb-set print elements 256",
                "-gdb-set mi-async on",
                "-gdb-set non-stop off",
            ):
                await driver.command(command, 5)
            try:
                await driver.command("-gdb-set host-charset UTF-8", 5)
                await driver.command("-gdb-set target-charset UTF-8", 5)
                driver.utf8 = True
            except RACPError as exc:
                if exc.error.code != "GDB_OPERATION_FAILED":
                    raise
            return driver
        except BaseException:
            await driver.close()
            raise

    def fail(self) -> None:
        self.state, self.reason = "FAILED", "gdb_transport_failed"
        self.changed.set()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    RACPError(
                        "GDB_TRANSPORT_FAILED",
                        "GDB exited or protocol failed",
                        layer="plugin",
                        execution_state="unknown",
                    )
                )

    def emit(self) -> None:
        self.changed.set()
        self.on_state(
            {
                "debugger_state": self.state,
                "stop_sequence": str(self.sequence),
                "stop_reason": self.reason,
            }
        )

    def observe(self, record: MIRecord) -> None:
        if record.kind == "=" and record.name == "thread-group-started":
            self.pid = int(record.data["pid"])
            self.birth = psutil.Process(self.pid).create_time()
        if record.kind == "*" and record.name == "running":
            self.state, self.reason = "RUNNING", "running"
            self.emit()
        if record.kind == "*" and record.name == "stopped":
            self.reason = str(record.data.get("reason", "stopped"))[:256]
            self.state = "EXITED" if self.reason.startswith("exited") else "STOPPED"
            self.sequence += 1
            frame = record.data.get("frame", {})
            self.address = frame.get("addr") if isinstance(frame, dict) else None
            self.emit()

    async def stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            while raw := await self.process.stdout.readline():
                if raw.strip() == b"(gdb)":
                    continue
                if not raw.lstrip(b"0123456789").startswith(
                    (b"^", b"*", b"+", b"=", b"~", b"@", b"&")
                ):
                    # Windows inferior console output can share this pipe; never
                    # treat its text as protocol or forward it to plugin stdout.
                    self.log_bytes += len(raw)
                    if self.log_bytes > 8 * 1024 * 1024:
                        raise ValueError("GDB target log budget exceeded")
                    continue
                record = parse_record(raw)
                if record.kind == "^":
                    future = self.pending.get(record.token or "")
                    if future is None or future.done():
                        raise ValueError("unsolicited GDB result")
                    future.set_result(record)
                elif record.kind in "~@&":
                    self.log_bytes += len(raw)
                    if self.log_bytes > 8 * 1024 * 1024:
                        raise ValueError("GDB stream budget exceeded")
                else:
                    self.observe(record)
            self.fail()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fail()

    async def stderr(self) -> None:
        assert self.process.stderr is not None
        while chunk := await self.process.stderr.read(8192):
            self.log_bytes += len(chunk)
            if self.log_bytes > 8 * 1024 * 1024:
                self.fail()
                return

    async def command(self, command: str, budget_seconds: float) -> dict[str, Any]:
        if "\n" in command or "\r" in command or "\x00" in command:
            raise ValueError("GDB command framing invalid")
        async with self.serial:
            if self.process.returncode is not None or self.state == "FAILED":
                raise RACPError(
                    "GDB_TRANSPORT_FAILED",
                    "GDB is not running",
                    layer="plugin",
                    execution_state="unknown",
                )
            self.token += 1
            token = str(self.token)
            future: asyncio.Future[MIRecord] = asyncio.get_running_loop().create_future()
            self.pending[token] = future
            assert self.process.stdin is not None
            try:
                async with asyncio.timeout(budget_seconds):
                    self.process.stdin.write((token + command + "\n").encode("utf-8"))
                    await self.process.stdin.drain()
                    result = await asyncio.shield(future)
                if result.name == "error":
                    raise RACPError(
                        "GDB_OPERATION_FAILED",
                        str(result.data.get("msg", "GDB rejected operation"))[:4096],
                        layer="plugin",
                        execution_state="unknown",
                    )
                return result.data
            finally:
                self.pending.pop(token, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()

    async def stopped(self, after: int, budget_seconds: float) -> None:
        async with asyncio.timeout(budget_seconds):
            while self.sequence <= after and self.state != "FAILED":
                self.changed.clear()
                if self.sequence > after:
                    break
                await self.changed.wait()
        if self.state not in {"STOPPED", "EXITED"}:
            raise RACPError(
                "GDB_TRANSPORT_FAILED",
                "GDB did not report a stop",
                layer="plugin",
                execution_state="unknown",
            )

    async def launch(self, target: Path, args: list[str], budget_seconds: float) -> None:
        if not self.utf8 and any(not value.isascii() for value in [str(target), *args]):
            raise RACPError(
                "OPERATION_NOT_SUPPORTED",
                "installed GDB lacks UTF-8 path/argv support",
                layer="plugin",
            )
        await self.command(
            "-file-exec-and-symbols "
            + json.dumps(str(target).replace("\\", "/"), ensure_ascii=False),
            budget_seconds,
        )
        await self.command(
            "-exec-arguments " + " ".join(json.dumps(arg, ensure_ascii=False) for arg in args),
            budget_seconds,
        )
        await self.command("-break-insert -t main", budget_seconds)
        before = self.sequence
        await self.command("-exec-run", budget_seconds)
        await self.stopped(before, budget_seconds)

    async def close(self) -> None:
        try:
            if self.state not in {"STARTING", "EXITED", "FAILED"} and self.pid is not None:
                if self.state == "RUNNING":
                    before = self.sequence
                    await self.command("-exec-interrupt --all", 3)
                    await self.stopped(before, 3)
                await self.command('-interpreter-exec console "kill"', 3)
            if self.process.returncode is None and self.state != "FAILED":
                await self.command("-gdb-exit", 3)
        finally:
            if self.process.returncode is None:
                self.process.kill()
            if self.process.stdin is not None:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            finally:
                for reader in self.readers:
                    reader.cancel()
                await asyncio.gather(*self.readers, return_exceptions=True)
