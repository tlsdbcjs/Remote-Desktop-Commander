from typing import Any

from pydantic import Field, model_validator
from racp_domain.jobs import JobState

from racp_protocol.models import Identifier, StrictModel


class JobAcceptance(StrictModel):
    operation_id: Identifier
    job_id: Identifier
    device_id: Identifier
    state: JobState
    poll_after_ms: int | None = Field(ge=1)


class JobView(JobAcceptance):
    id: Identifier
    owner_id: Identifier
    revision: int = Field(ge=1)
    progress: float | None = Field(ge=0, le=1)
    waiting_reason: Identifier | None
    progress_revision: int = Field(ge=0)
    operation: str
    timeout_ms: int = Field(ge=1, le=86400000)
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: str = Field(max_length=64)
    updated_at: str = Field(max_length=64)
    outcome_available: bool = True

    @model_validator(mode="after")
    def valid_wait(self) -> "JobView":
        if (self.state == JobState.WAITING) != (self.waiting_reason is not None):
            raise ValueError("only WAITING requires waiting_reason")
        if self.id != self.job_id:
            raise ValueError("job ID differs")
        return self
