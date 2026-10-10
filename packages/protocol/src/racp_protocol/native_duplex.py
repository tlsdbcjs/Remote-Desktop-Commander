"""Bounded internal native-channel frames; not a public operation or auth token."""

import base64
import binascii
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from racp_protocol.models import Identifier, StrictModel, WorkspaceId

DUPLEX_CHUNK_BYTES = 16384
DUPLEX_WINDOW_FRAMES = 4
DUPLEX_MAX_CHANNELS = 16
Counter = Annotated[int, Field(ge=0, le=2**63 - 1)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DuplexScope(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    session_id: Identifier
    device_id: Identifier
    agent_boot_id: Identifier
    connection_epoch: Annotated[int, Field(ge=1, le=2**63 - 1)]
    principal_id: Identifier
    workspace_id: WorkspaceId
    permission_revision: Fingerprint
    endpoint_role: Literal["agent_listener", "agent_connector"] = "agent_listener"

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class DuplexFrame(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    scope_fingerprint: Fingerprint
    channel_id: Annotated[int, Field(ge=1, le=DUPLEX_MAX_CHANNELS)]


class DuplexOpen(DuplexFrame):
    type: Literal["native.open"] = "native.open"


class DuplexData(DuplexFrame):
    type: Literal["native.data"] = "native.data"
    byte_offset: Counter
    data_base64: Annotated[str, Field(min_length=4, max_length=21848)]

    @model_validator(mode="after")
    def validate_data(self) -> "DuplexData":
        try:
            raw = base64.b64decode(self.data_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("native payload must be canonical base64") from None
        if not 1 <= len(raw) <= DUPLEX_CHUNK_BYTES:
            raise ValueError("native chunk exceeds bound")
        if base64.b64encode(raw).decode("ascii") != self.data_base64:
            raise ValueError("noncanonical native payload")
        if self.byte_offset + len(raw) > 2**63 - 1:
            raise ValueError("native offset overflow")
        return self

    @property
    def decoded(self) -> bytes:
        return base64.b64decode(self.data_base64, validate=True)


class DuplexAck(DuplexFrame):
    type: Literal["native.ack"] = "native.ack"
    byte_offset: Counter


class DuplexEnd(DuplexFrame):
    type: Literal["native.end"] = "native.end"
    byte_offset: Counter


NativeFrame = DuplexOpen | DuplexData | DuplexAck | DuplexEnd
_ADAPTER: TypeAdapter[NativeFrame] = TypeAdapter(
    Annotated[NativeFrame, Field(discriminator="type")]
)


def parse_duplex_frame(value: dict[str, Any]) -> NativeFrame:
    return _ADAPTER.validate_python(value)
