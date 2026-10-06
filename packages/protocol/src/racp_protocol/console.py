from typing import Any, Literal

from pydantic import Field

from racp_protocol.models import Identifier, StrictModel


class ListPage[Item](StrictModel):
    items: list[Item] = Field(max_length=500)
    next_cursor: str | None
    consistency: Literal["best_effort"] = "best_effort"
    sort: Literal["rowid_desc"] = "rowid_desc"


class DeviceView(StrictModel):
    id: Identifier
    name: str
    owner_id: Identifier
    revoked: int
    epoch: int
    info: dict[str, Any]


class ArtifactView(StrictModel):
    id: Identifier
    operation_id: Identifier
    device_id: Identifier
    owner_id: Identifier
    sha256: str
    size_bytes: int = Field(ge=0, le=1024**3)
    created_at: str
    media_type: str
    state: str
    expires_at: str


class AuditView(StrictModel):
    id: Identifier
    timestamp: str
    event: str
    device_id: str
    operation_id: str
    operation: str
    trace_id: str
    request_id: str
    owner_id: Identifier
    summary: dict[str, Any]


class ApprovalView(StrictModel):
    id: Identifier
    operation_id: Identifier
    digest: str
    expires: float
    state: str
    device_id: Identifier
    device_name: str
    execution_identity: str | None
    agent_boot_id: str | None
    operation: str
    profile: str
    target: dict[str, Any]


class ConsoleLogin(StrictModel):
    setup_secret: str = Field(min_length=20, max_length=128)


class ConsoleSession(StrictModel):
    owner_id: Identifier
    csrf_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    idle_seconds: int = 1800
    absolute_seconds: int = 43200
    expires_at: str = Field(max_length=64)


class ConsoleEvent(StrictModel):
    event_id: str = Field(max_length=128)
    type: Literal["change", "ready", "refresh", "session_expired"]
    observed_at: str = Field(max_length=64)
    resource: str = Field(max_length=128)
    device_id: Identifier | None = None
    operation_id: Identifier | None = None
    full_refresh: bool = False


class DoctorView(StrictModel):
    status: Literal["healthy", "degraded"]
    protocol: int = 1
    owner_id: Identifier
    gateway: dict[str, Any]
    devices: list[dict[str, Any]] = Field(max_length=500)
    limitations: list[str]
