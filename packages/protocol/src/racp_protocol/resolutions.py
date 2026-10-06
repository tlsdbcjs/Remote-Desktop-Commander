from typing import Any, Literal

from pydantic import Field

from racp_protocol.models import Identifier, StrictModel


class ResolutionView(StrictModel):
    id: Identifier
    operation_id: Identifier
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "UNKNOWN"]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    observed_at: str = Field(max_length=64)
    outcome_available: bool


class OperationResolutions(StrictModel):
    operation_id: Identifier
    state: Literal[
        "ACCEPTED",
        "DISPATCHED",
        "RUNNING",
        "CANCEL_REQUESTED",
        "RECONCILING",
        "SUCCEEDED",
        "FAILED",
        "TIMED_OUT",
        "CANCELLED",
        "UNKNOWN",
    ]
    items: list[ResolutionView] = Field(max_length=16)
