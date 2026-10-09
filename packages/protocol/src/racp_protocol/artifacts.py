from typing import Literal

from pydantic import Field, model_validator

from racp_protocol.models import Identifier, StrictModel
from racp_protocol.provider_models import Sha256

CHUNK_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 1024**3
ARTIFACT_INPUT_OPERATIONS = frozenset({"filesystem.write", "browser.upload"})


class TransferCreate(StrictModel):
    device_id: Identifier
    direction: Literal["upload", "download"] = "upload"
    operation_id: Identifier | None = None
    output_id: Identifier | None = None
    artifact_id: Identifier | None = None
    size_bytes: int | None = Field(default=None, ge=0, le=MAX_ARTIFACT_BYTES)
    sha256: Sha256 | None = None
    media_type: Literal[
        "application/octet-stream",
        "application/vnd.tcpdump.pcap",
        "application/json",
        "application/vnd.racp.output-stream",
        "image/png",
        "image/jpeg",
        "text/plain",
    ] = "application/octet-stream"

    @model_validator(mode="after")
    def validate_direction(self) -> "TransferCreate":
        if self.output_id and (self.direction != "upload" or not self.operation_id):
            raise ValueError("output_id requires an operation upload")
        if self.direction == "upload":
            if self.size_bytes is None or self.sha256 is None or self.artifact_id is not None:
                raise ValueError("upload requires size/hash and excludes artifact_id")
        elif self.artifact_id is None or self.size_bytes is not None or self.sha256 is not None:
            raise ValueError("download requires artifact_id and excludes size/hash")
        return self


class TransferComplete(StrictModel):
    size_bytes: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)
    sha256: Sha256
