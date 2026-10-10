import codecs
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_INLINE_BYTES = 64 * 1024
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")]
WorkspaceId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")]
TraceId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ShellInput(StrictModel):
    mode: Literal["argv", "shell"] = "argv"
    argv: list[Annotated[str, Field(max_length=32768)]] | None = None
    command: Annotated[str, Field(max_length=65536)] | None = None
    shell: Literal["powershell", "pwsh", "cmd", "bash"] | None = None
    cwd: Annotated[str, Field(max_length=4096)] | None = None
    env: dict[str, str | None] = Field(default_factory=dict)
    encoding: str = "utf-8"
    max_output_bytes: int = Field(default=MAX_INLINE_BYTES, ge=1024, le=MAX_INLINE_BYTES)

    @model_validator(mode="after")
    def validate_mode(self) -> "ShellInput":
        if self.mode == "argv":
            if not self.argv or len(self.argv) > 256 or not self.argv[0]:
                raise ValueError("argv must have 1–256 arguments")
            if self.command is not None or self.shell is not None:
                raise ValueError("argv excludes command and shell")
        elif self.argv is not None or not self.command or self.shell is None:
            raise ValueError("shell mode requires command and shell, excludes argv")
        codecs.lookup(self.encoding)
        if len(self.env) > 128:
            raise ValueError("too many environment overrides")
        for key, value in self.env.items():
            if not key or "=" in key or "\0" in key or len(key) > 256:
                raise ValueError("invalid environment key")
            if value is not None and ("\0" in value or len(value) > 32768):
                raise ValueError("invalid environment value")
        for arg in (self.argv or []) + [self.command or "", self.cwd or ""]:
            if "\0" in arg:
                raise ValueError("NUL is forbidden")
        return self


class OperationInput(StrictModel):
    device_id: Identifier
    workspace_id: WorkspaceId = "default"
    operation: Annotated[str, Field(pattern=r"^[a-z]+\.[a-z_]+$", max_length=96)]
    payload: dict[str, Any]
    timeout_ms: int | None = Field(default=None, ge=1, le=86400000)
    execution_mode: Literal["sync", "job"] = "sync"
    idempotency_key: (
        Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")] | None
    ) = None
    execution_profile_id: Literal["read_only", "standard", "trusted_personal"] = "read_only"
    approval_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_budget(self) -> "OperationInput":
        if (
            self.execution_mode == "sync"
            and self.timeout_ms is not None
            and self.timeout_ms > 120000
        ):
            raise ValueError("synchronous budget exceeds 120 seconds; choose job before execution")
        return self

    @property
    def budget_ms(self) -> int:
        if self.timeout_ms is not None:
            return self.timeout_ms
        if self.execution_mode == "job":
            return 3600000
        return {
            "shell.exec": 60000,
            "filesystem.stat": 5000,
            "browser.click": 10000,
            "browser.type": 10000,
            "browser.key": 10000,
        }.get(self.operation, 30000)


class Capability(StrictModel):
    name: Identifier
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    operations: list[str]
    installed: bool = True
    supported: bool = True
    enabled: bool = True
    healthy: bool = True
    unavailable_reason: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Wire(StrictModel):
    protocol: Literal[1] = 1
    device_id: Identifier
    agent_boot_id: Identifier


class Hello(Wire):
    type: Literal["hello"] = "hello"
    agent_version: str
    supported_protocols: list[int]
    platform: str
    architecture: str
    execution_identity: str
    capabilities: list[Capability]
    last_event_cursor: str = "0"


class Connected(Wire):
    connection_epoch: int = Field(ge=1)


class Welcome(Connected):
    type: Literal["welcome"] = "welcome"
    heartbeat_interval_ms: int = 5000
    max_message_bytes: int = MAX_MESSAGE_BYTES
    execution_lease_ttl_ms: int = 60000


class ResourceHandle(StrictModel):
    id: Identifier
    type: Literal[
        "terminal", "interactive-process", "browser", "browser-page", "analysis", "debugger"
    ]
    device_id: Identifier
    owner: Identifier
    agent_boot_id: Identifier
    workspace_id: WorkspaceId | None = None
    provider_instance_id: Identifier
    resource_revision: str = Field(pattern=r"^\d{1,20}$")
    created_at: str = Field(max_length=64)
    last_access_at: str = Field(max_length=64)
    expires_at: str = Field(max_length=64)
    state: Literal["CREATING", "ACTIVE", "CLOSING", "CLOSED", "EXPIRED", "FAILED"]
    availability: Literal["available", "unavailable", "offline", "exited"]
    pid: int | None = Field(default=None, ge=1)
    create_time: float | None = None
    exit_code: int | None = None
    output: Literal["discard"] | None = None
    ownership: Literal["racp_owned", "borrowed", "external_browser_owned_context"] | None = None
    backend: str | None = Field(default=None, max_length=64)
    backend_version: str | None = Field(default=None, max_length=128)
    target_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    architecture: str | None = Field(default=None, max_length=64)
    image_base: str | None = Field(default=None, pattern=r"^0x[0-9a-fA-F]{1,16}$")
    analysis_database: str | None = Field(default=None, max_length=32768)
    analysis_state: Literal["ANALYZING", "READY", "FAILED", "CLOSED"] | None = None
    debugger_state: (
        Literal["STARTING", "STOPPED", "RUNNING", "DETACHED", "EXITED", "FAILED"] | None
    ) = None
    stop_sequence: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]{0,18})$")
    stop_reason: str | None = Field(default=None, max_length=256)


class Heartbeat(Connected):
    type: Literal["heartbeat"] = "heartbeat"
    health: Literal["healthy", "degraded"] = "healthy"
    handles: list[ResourceHandle] | None = Field(default=None, max_length=128)
    capabilities: list[Capability] | None = Field(default=None, max_length=16)


class BrowserState(Connected):
    type: Literal["browser_state"] = "browser_state"
    provider_instance_id: Identifier
    event_sequence: str = Field(pattern=r"^\d{1,20}$")
    browser_id: Identifier | None = None
    kind: Literal[
        "health",
        "inventory",
        "gap",
        "navigation",
        "frame",
        "page_created",
        "page_closed",
        "dialog",
        "download",
    ]
    state: Literal[
        "observed",
        "changed",
        "active",
        "closed",
        "failed",
        "expired",
        "dismissed",
        "completed",
        "unsolicited_cancelled",
        "pending",
        "refresh_required",
        "files_verified",
        "unavailable",
    ]
    handles: list[ResourceHandle] = Field(default_factory=list, max_length=128)
    capability: Capability | None = None

    @model_validator(mode="after")
    def browser_scope(self) -> "BrowserState":
        if int(self.event_sequence) > 9223372036854775807:
            raise ValueError("browser sequence exceeds persistence bound")
        if self.kind == "health" and (self.capability is None or self.capability.name != "browser"):
            raise ValueError("browser health requires its capability")
        if self.kind not in {"health", "gap"} and self.capability is not None:
            raise ValueError("capability changes require health event")
        return self


class BrowserStateAck(Connected):
    type: Literal["browser_state_ack"] = "browser_state_ack"
    provider_instance_id: Identifier
    event_sequence: str = Field(pattern=r"^\d{1,20}$")


class Context(StrictModel):
    principal_id: Identifier
    workspace_id: WorkspaceId = "default"
    execution_profile_id: Literal["read_only", "standard", "trusted_personal"]
    policy_revision: int = Field(ge=1)


class Correlated(Connected):
    request_id: Identifier
    operation_id: Identifier
    trace_id: TraceId


class Request(Correlated):
    type: Literal["request"] = "request"
    timestamp: str
    operation: str
    timeout_ms: int = Field(ge=1, le=86400000)
    remaining_timeout_ms: int = Field(ge=1, le=86400000)
    execution_mode: Literal["sync", "job"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    context: Context
    payload: dict[str, Any]
    job_id: Identifier | None = None
    retain_key: bool = True


class Ack(Correlated):
    type: Literal["ack"] = "ack"


class OutputDescriptor(StrictModel):
    id: Identifier
    size_bytes: int = Field(ge=0, le=1024**3)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal[
        "application/octet-stream",
        "application/vnd.tcpdump.pcap",
        "application/json",
        "application/vnd.racp.output-stream",
        "image/png",
        "image/jpeg",
        "text/plain",
    ]
    artifact_id: Identifier | None = None


class Result(Correlated):
    type: Literal["result"] = "result"
    state: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "UNKNOWN"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    outputs: list[OutputDescriptor] = Field(default_factory=list, max_length=4)


class OutcomeExpired(Correlated):
    type: Literal["error"] = "error"
    code: Literal["OPERATION_EXPIRED"] = "OPERATION_EXPIRED"


class Cancel(Connected):
    type: Literal["cancel"] = "cancel"
    request_id: Identifier
    target_operation_id: Identifier
    reason: Literal["requested", "deadline", "revoked"] = "requested"


class ExecutionSnapshot(StrictModel):
    operation_id: Identifier
    request_id: Identifier
    trace_id: TraceId
    state: Literal["ACCEPTED", "RUNNING", "CANCEL_REQUESTED"]
    job_state: Literal["RUNNING", "WAITING"] | None = None
    waiting_reason: Identifier | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    progress_revision: int = Field(default=0, ge=0)


class JobUpdate(Correlated):
    type: Literal["event"] = "event"
    name: Literal["job.state_changed"] = "job.state_changed"
    state: Literal["RUNNING", "WAITING"]
    waiting_reason: Identifier | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def waiting_contract(self) -> "JobUpdate":
        if (self.state == "WAITING") != (self.waiting_reason is not None):
            raise ValueError("only WAITING requires waiting_reason")
        return self


class Reconcile(Connected):
    type: Literal["reconcile"] = "reconcile"
    records: list[Result] = Field(default_factory=list, max_length=500)
    complete: bool = True
    handles: list[ResourceHandle] = Field(default_factory=list, max_length=128)
    active: list[ExecutionSnapshot] = Field(default_factory=list, max_length=16)
    expired: list[OutcomeExpired] = Field(default_factory=list, max_length=500)


ByteOffset = Annotated[str, Field(pattern=r"^\d{1,20}$")]
STREAM_WINDOW_BYTES = 256 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
STREAM_IN_FLIGHT_FRAMES = 4


class StreamOpenInput(StrictModel):
    type: Literal["stream_open"] = "stream_open"
    cursor: ByteOffset = "0"
    max_bytes: int = Field(default=STREAM_CHUNK_BYTES, ge=4, le=STREAM_CHUNK_BYTES)
    window_bytes: int = Field(default=STREAM_WINDOW_BYTES, ge=1024, le=STREAM_WINDOW_BYTES)


class StreamIdentity(Connected):
    stream_id: Identifier
    handle_id: Identifier


class StreamSubscribe(StreamIdentity):
    type: Literal["stream_open"] = "stream_open"
    cursor: ByteOffset
    max_bytes: int = Field(ge=4, le=STREAM_CHUNK_BYTES)
    window_bytes: int = Field(ge=1024, le=STREAM_WINDOW_BYTES)
    context: Context


class StreamOpened(StreamIdentity):
    type: Literal["stream_opened"] = "stream_opened"
    cursor: ByteOffset
    max_bytes: int = Field(ge=4, le=STREAM_CHUNK_BYTES)
    window_bytes: int = Field(ge=1024, le=STREAM_WINDOW_BYTES)


class StreamData(StreamIdentity):
    type: Literal["stream_data"] = "stream_data"
    byte_offset: ByteOffset
    next_cursor: ByteOffset
    data: str = Field(max_length=STREAM_CHUNK_BYTES)
    invalid_byte_replacements: int = Field(default=0, ge=0, le=STREAM_CHUNK_BYTES)
    eof: bool = False
    process_exit: int | None = None

    @model_validator(mode="after")
    def bounded(self) -> "StreamData":
        size = int(self.next_cursor) - int(self.byte_offset)
        if size <= 0 or size > STREAM_CHUNK_BYTES:
            raise ValueError("invalid stream byte interval")
        return self


class StreamAck(StreamIdentity):
    type: Literal["stream_ack"] = "stream_ack"
    byte_offset: ByteOffset


class StreamAckInput(StrictModel):
    type: Literal["stream_ack"] = "stream_ack"
    stream_id: Identifier
    byte_offset: ByteOffset


class StreamUnsubscribe(StreamIdentity):
    type: Literal["stream_unsubscribe"] = "stream_unsubscribe"


class StreamGap(StreamIdentity):
    type: Literal["stream_gap"] = "stream_gap"
    byte_offset: ByteOffset
    earliest_cursor: ByteOffset
    lost_bytes: ByteOffset


class StreamEnd(StreamIdentity):
    type: Literal["stream_end"] = "stream_end"
    byte_offset: ByteOffset
    reason: Literal["eof", "unsubscribed", "error"]
    error: dict[str, Any] | None = None
    process_exit: int | None = None


class NativeSubscribe(StreamIdentity):
    type: Literal["native_subscribe"] = "native_subscribe"
    principal_id: Identifier
    workspace_id: WorkspaceId


class NativeOpened(StreamIdentity):
    type: Literal["native_opened"] = "native_opened"
    scope: dict[str, Any]

    @model_validator(mode="after")
    def bound_scope(self) -> "NativeOpened":
        from racp_protocol.native_duplex import DuplexScope

        scope = DuplexScope.model_validate(self.scope)
        if (scope.session_id, scope.device_id, scope.agent_boot_id, scope.connection_epoch) != (
            self.handle_id, self.device_id, self.agent_boot_id, self.connection_epoch
        ):
            raise ValueError("native scope and envelope differ")
        return self


class NativePacket(StreamIdentity):
    type: Literal["native_packet"] = "native_packet"
    frame: dict[str, Any]

    @model_validator(mode="after")
    def bounded_frame(self) -> "NativePacket":
        from racp_protocol.native_duplex import parse_duplex_frame

        parse_duplex_frame(self.frame)
        return self


class NativeUnsubscribe(StreamIdentity):
    type: Literal["native_unsubscribe"] = "native_unsubscribe"


NativeStopReason = Literal["closed", "error", "revoked", "disconnected", "expired", "overflow"]


class NativeStopped(StreamIdentity):
    type: Literal["native_stopped"] = "native_stopped"
    reason: NativeStopReason


Message = Annotated[
    Hello
    | Welcome
    | Heartbeat
    | BrowserState
    | BrowserStateAck
    | Request
    | Ack
    | Result
    | OutcomeExpired
    | Cancel
    | Reconcile
    | JobUpdate
    | StreamSubscribe
    | StreamOpened
    | StreamData
    | StreamAck
    | StreamUnsubscribe
    | StreamGap
    | StreamEnd
    | NativeSubscribe
    | NativeOpened
    | NativePacket
    | NativeUnsubscribe
    | NativeStopped,
    Field(discriminator="type"),
]
MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)


def validate_tree(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("maximum JSON nesting exceeded")
    if isinstance(value, str) and len(value.encode("utf-8")) > 256 * 1024:
        raise ValueError("maximum string size exceeded")
    if isinstance(value, dict):
        for key, item in value.items():
            validate_tree(key, depth + 1)
            validate_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_tree(item, depth + 1)


def decode_message(raw: str | bytes) -> Message:
    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size > MAX_MESSAGE_BYTES:
        raise ValueError("maximum frame size exceeded")
    value = json.loads(raw)
    validate_tree(value)
    return MESSAGE_ADAPTER.validate_python(value)
