"""Backend interfaces; ownership/policy/journal remain in the Agent application layer."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class Target:
    path: Path
    sha256: str


@dataclass(frozen=True)
class AnalysisSession:
    id: str
    architecture: str
    image_base: str
    database: Path
    state: Literal["ANALYZING", "READY", "FAILED", "CLOSED"]


@dataclass(frozen=True)
class FunctionInfo:
    address: str
    name: str
    size_bytes: int


@dataclass(frozen=True)
class StringEntry:
    address: str
    value: str


@dataclass(frozen=True)
class Xref:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None


class StaticAnalysisBackend(Protocol):
    async def open(self, target: Target) -> AnalysisSession: ...
    async def close(self, session: str) -> None: ...
    async def info(self, session: str) -> AnalysisSession: ...
    async def functions(
        self, session: str, limit: int, cursor: str | None
    ) -> Page[FunctionInfo]: ...
    async def strings(self, session: str, limit: int, cursor: str | None) -> Page[StringEntry]: ...
    async def xrefs(
        self, session: str, address: str, limit: int, cursor: str | None
    ) -> Page[Xref]: ...
    async def disassemble(self, session: str, address: str) -> str: ...
    async def decompile(self, session: str, address: str) -> str: ...
    async def rename(self, session: str, address: str, name: str) -> None: ...
    async def comment(self, session: str, address: str, text: str) -> None: ...


@dataclass(frozen=True)
class DebuggerStatus:
    state: Literal["STARTING", "STOPPED", "RUNNING", "DETACHED", "EXITED", "FAILED"]
    sequence: str
    pid: int | None = None
    create_time: float | None = None


@dataclass(frozen=True)
class StopEvent:
    sequence: str
    reason: str
    address: str | None


class DebuggerBackend(Protocol):
    async def launch(self, target: Target, args: tuple[str, ...]) -> str: ...
    async def attach(self, pid: int, create_time: float) -> str: ...
    async def close(self, session: str) -> None: ...
    async def info(self, session: str) -> DebuggerStatus: ...
    async def continue_(self, session: str) -> DebuggerStatus: ...
    async def step_into(self, session: str) -> DebuggerStatus: ...
    async def step_over(self, session: str) -> DebuggerStatus: ...
    async def set_breakpoint(self, session: str, address: str) -> str: ...
    async def remove_breakpoint(self, session: str, id: str) -> None: ...
    async def registers(self, session: str) -> dict[str, str]: ...
    async def read_memory(self, session: str, address: str, size_bytes: int) -> bytes: ...
    async def backtrace(self, session: str) -> tuple[tuple[str, str], ...]: ...
    async def wait(self, session: str, after_sequence: str) -> StopEvent: ...
