import base64
import hashlib
import json
import time

import pytest
from pydantic import ValidationError
from racp_agent.broker.blobs import CaptureBlobs
from racp_agent.broker.capture import png, preview
from racp_domain.models import RACPError
from racp_protocol.desktop import DesktopScreenshot


def test_capture_blob_scope_chunks_fixed_ttl_and_quota() -> None:
    blobs = CaptureBlobs()
    data = bytes(range(256)) * 512
    descriptor = blobs.put(data, "owner_test", "device_test", "op_test")
    identifier = descriptor["capture_id"]
    assert descriptor["sha256"] == hashlib.sha256(data).hexdigest()
    expiry = blobs.blobs[identifier].expires
    reconstructed = bytearray()
    offset = 0
    while offset < len(data):
        reply = blobs.read(
            {"capture_id": identifier, "offset": offset}, "owner_test", "device_test", "op_test"
        )
        assert len(json.dumps(reply).encode()) < 65536
        reconstructed.extend(base64.b64decode(reply["data"]))
        offset = reply["next_offset"]
        assert reply["eof"] == (offset == len(data))
    assert reconstructed == data and blobs.blobs[identifier].expires == expiry
    for scope in [
        ("other", "device_test", "op_test"),
        ("owner_test", "other", "op_test"),
        ("owner_test", "device_test", "other"),
    ]:
        with pytest.raises(RACPError) as denied:
            blobs.read({"capture_id": identifier, "offset": 0}, *scope)
        assert denied.value.error.code == "PERMISSION_DENIED"
    with pytest.raises(RACPError):
        blobs.read(
            {"capture_id": identifier, "offset": len(data) + 1},
            "owner_test",
            "device_test",
            "op_test",
        )
    with pytest.raises(ValidationError):
        blobs.read(
            {"capture_id": identifier, "offset": 0, "length": 32769},
            "owner_test",
            "device_test",
            "op_test",
        )
    blobs.limit_bytes = len(data)
    with pytest.raises(RACPError):
        blobs.put(b"x", "owner_test", "device_test", "op_test")
    blobs.blobs[identifier].expires = time.monotonic() - 1
    with pytest.raises(RACPError) as expired:
        blobs.read({"capture_id": identifier, "offset": 0}, "owner_test", "device_test", "op_test")
    assert expired.value.error.code == "HANDLE_EXPIRED" and not blobs.blobs


def test_capture_dimension_bound_and_preview_transform() -> None:
    with pytest.raises(RACPError):
        png(-1, -1, bytes(4))
    with pytest.raises(RACPError):
        png(16385, 1, bytes(4))
    with pytest.raises(RACPError):
        png(2, 2, bytes(4))
    pixels = bytes([10, 20, 30, 255]) * (1920 * 2)
    data, metadata = preview(1920, 2, pixels)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert metadata["width"] == 1600 and metadata["height"] == 1
    assert metadata["physical_pixels_per_preview_pixel_x"] == 1.2
    assert metadata["physical_pixels_per_preview_pixel_y"] == 2
    with pytest.raises(ValidationError):
        DesktopScreenshot(session_id=1, monitor_id="monitor_test", window_id="window_test")
