"""Short-lived owner/Device/operation-scoped capture bytes, never arbitrary files."""

import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Any

from pydantic import Field
from racp_domain.models import RACPError
from racp_protocol.models import Identifier, StrictModel, new_id


class BlobRead(StrictModel):
    capture_id: Identifier
    offset: int = Field(ge=0, le=32 * 1024 * 1024)
    length: int = Field(default=32768, ge=1, le=32768)


class BlobRelease(StrictModel):
    capture_id: Identifier


@dataclass
class Blob:
    owner: str
    device: str
    operation: str
    expires: float
    data: bytes


class CaptureBlobs:
    def __init__(self) -> None:
        self.blobs: dict[str, Blob] = {}
        self.limit_bytes = 64 * 1024 * 1024

    def collect(self) -> None:
        self.blobs = {
            key: value for key, value in self.blobs.items() if value.expires > time.monotonic()
        }

    def put(self, data: bytes, owner: str, device: str, operation: str) -> dict[str, Any]:
        self.collect()
        if (
            not 0 < len(data) <= 32 * 1024 * 1024
            or len(self.blobs) >= 4
            or sum(len(b.data) for b in self.blobs.values()) + len(data) > self.limit_bytes
        ):
            raise RACPError("RESOURCE_EXHAUSTED", "Broker capture buffer limit", layer="broker")
        identifier = new_id("capture")
        self.blobs[identifier] = Blob(owner, device, operation, time.monotonic() + 30, data)
        return {
            "capture_id": identifier,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "media_type": "image/png",
            "ttl_ms": 30000,
        }

    def scoped(self, identifier: str, owner: str, device: str, operation: str) -> Blob:
        self.collect()
        blob = self.blobs.get(identifier)
        if blob is None:
            raise RACPError("HANDLE_EXPIRED", "capture buffer expired", layer="broker")
        if (owner, device, operation) != (blob.owner, blob.device, blob.operation):
            raise RACPError("PERMISSION_DENIED", "capture operation scope mismatch", layer="broker")
        return blob

    def read(
        self, payload: dict[str, Any], owner: str, device: str, operation: str
    ) -> dict[str, Any]:
        value = BlobRead.model_validate(payload)
        blob = self.scoped(value.capture_id, owner, device, operation)
        if value.offset > len(blob.data):
            raise RACPError("INVALID_ARGUMENT", "capture offset exceeds length", layer="broker")
        data = blob.data[value.offset : value.offset + value.length]
        return {
            "offset": value.offset,
            "data": base64.b64encode(data).decode("ascii"),
            "next_offset": value.offset + len(data),
            "eof": value.offset + len(data) == len(blob.data),
        }

    def release(self, payload: dict[str, Any], owner: str, device: str, operation: str) -> None:
        value = BlobRelease.model_validate(payload)
        self.scoped(value.capture_id, owner, device, operation)
        del self.blobs[value.capture_id]
