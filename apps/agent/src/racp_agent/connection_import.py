"""Import a user-selected connection file without returning its token to the renderer."""

import hashlib
import hmac
import os
import stat
from pathlib import Path

from pydantic import ValidationError
from racp_domain.models import RACPError
from racp_sdk.connection_file import MAX_CONNECTION_BYTES, ConnectionFile

from racp_agent.settings import local_path


def load_connection(path: Path, expected_sha256: str | None = None) -> tuple[ConnectionFile, str]:
    try:
        source = local_path(path)
        if not stat.S_ISREG(source.lstat().st_mode):
            raise ValueError("Connection file must be a regular local file")
        with source.open("rb") as stream:
            raw = stream.read(MAX_CONNECTION_BYTES + 1)
        if len(raw) > MAX_CONNECTION_BYTES:
            raise ValueError("Connection file exceeds its bound")
        fingerprint = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and not hmac.compare_digest(fingerprint, expected_sha256):
            raise RACPError("CONNECTION_FILE_CHANGED", "Select the changed connection file again")
        value = ConnectionFile.model_validate_json(raw)
    except (OSError, ValueError, ValidationError):
        raise RACPError("CONNECTION_FILE_INVALID", "Invalid local connection file") from None
    if value.expired():
        raise RACPError("CONNECTION_FILE_EXPIRED", "Connection file has expired")
    return value, fingerprint


def imported_ca(root: Path, value: ConnectionFile) -> Path | None:
    if value.ca_pem is None:
        return None
    raw = value.ca_pem.encode("utf-8")
    target = local_path(root / ("gateway-ca-" + hashlib.sha256(raw).hexdigest() + ".pem"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if target.read_bytes() != raw:
            raise RACPError("CONNECTION_FILE_INVALID", "Imported CA changed") from None
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return target
