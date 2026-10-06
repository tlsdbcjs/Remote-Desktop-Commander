import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from racp_domain.models import ExecutionContext
from racp_protocol.models import Identifier, ResourceHandle, StrictModel, timestamp
from racp_protocol.reversing import Address, DebuggerState


class BackendResource(StrictModel):
    resource_id: Identifier
    target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    architecture: str = Field(min_length=1, max_length=64)
    image_base: Address
    analysis_database: str | None = Field(default=None, max_length=32768)
    analysis_state: Literal["ANALYZING", "READY", "FAILED", "CLOSED"] | None = None
    debugger_state: DebuggerState | None = None
    pid: int | None = Field(default=None, ge=1, le=0xFFFFFFFF)
    create_time: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    stop_sequence: str = Field(default="0", pattern=r"^(0|[1-9][0-9]{0,18})$")
    stop_reason: str | None = Field(default=None, max_length=256)


@dataclass
class ManagedResource:
    id: str
    kind: Literal["analysis", "debugger"]
    backend: str
    instance: str
    context: ExecutionContext
    descriptor: BackendResource
    directory: Path
    ownership: Literal["racp_owned", "borrowed"] = "racp_owned"
    state: str = "ACTIVE"
    revision: int = 1
    created: str = field(default_factory=timestamp)
    accessed: str = field(default_factory=timestamp)
    expires_monotonic: float = field(default_factory=lambda: time.monotonic() + 3600)
    expires: str = field(
        default_factory=lambda: (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    )
    stop_events: list[dict[str, Any]] = field(default_factory=list)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def renew(self) -> None:
        self.accessed = timestamp()
        self.expires_monotonic = time.monotonic() + 3600
        self.expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    def handle(self, backend_version: str) -> ResourceHandle:
        return ResourceHandle.model_validate(
            {
                "id": self.id,
                "type": self.kind,
                "device_id": self.context.device_id,
                "owner": self.context.principal_id,
                "agent_boot_id": self.context.agent_boot_id,
                "provider_instance_id": self.instance,
                "resource_revision": str(self.revision),
                "created_at": self.created,
                "last_access_at": self.accessed,
                "expires_at": self.expires,
                "state": self.state,
                "availability": "available" if self.state == "ACTIVE" else "unavailable",
                "ownership": self.ownership,
                "backend": self.backend,
                "backend_version": backend_version,
                **self.descriptor.model_dump(exclude={"resource_id"}),
            }
        )
