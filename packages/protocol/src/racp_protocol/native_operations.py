"""Managed native bridge operation inputs; arbitrary native command is broad OS access."""

from typing import Annotated

from pydantic import BaseModel, Field

from racp_protocol.models import Identifier, StrictModel
from racp_protocol.provider_models import ProcessTarget


class NativePrepare(ProcessTarget):
    max_bytes: Annotated[int, Field(ge=16384, le=64 * 1024**2)] = 8 * 1024**2
    lease_seconds: Annotated[int, Field(ge=10, le=3600)] = 120


class NativeTarget(StrictModel):
    handle_id: Identifier


NATIVE_MODELS: dict[str, type[BaseModel]] = {
    "native.prepare": NativePrepare,
    "native.start": NativeTarget,
    "native.close": NativeTarget,
}
