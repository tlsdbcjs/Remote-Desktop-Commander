from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class OperationState(StrEnum):
    ACCEPTED = "ACCEPTED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.TIMED_OUT,
        OperationState.UNKNOWN,
    }
)


@dataclass(frozen=True)
class Error:
    code: str
    message: str
    layer: str = "gateway"
    retryable: bool = False
    execution_state: str = "not_started"
    details: dict[str, Any] = field(default_factory=dict)


class RACPError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        layer: str = "gateway",
        execution_state: str = "not_started",
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.error = Error(code, message, layer, False, execution_state, details)


@dataclass(frozen=True)
class ExecutionContext:
    operation_id: str
    request_id: str
    trace_id: str
    device_id: str
    principal_id: str
    agent_boot_id: str
    timeout_ms: int
    workspace_id: str = "default"


class Provider(Protocol):
    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]: ...


class AgentChannel(Protocol):
    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class ArtifactCatalog(Protocol):
    def ready(self, artifact_id: str, owner: str) -> dict[str, Any]: ...

    def observe_outputs(self, outputs: list[dict[str, Any]], operation: dict[str, Any]) -> None: ...

    def outputs(self, operation_id: str, owner: str) -> list[dict[str, Any]]: ...

    async def png_preview(
        self, artifact_id: str, owner: str, operation_id: str
    ) -> bytes | None: ...
