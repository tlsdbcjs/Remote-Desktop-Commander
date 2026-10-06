import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from racp_domain.models import RACPError


class CursorCodec:
    def __init__(self) -> None:
        self.key = secrets.token_bytes(32)

    def encode(self, offset: int, scope: str, revision: str) -> str:
        raw = json.dumps(
            {"offset": offset, "scope": scope, "revision": revision, "expires": time.time() + 300},
            separators=(",", ":"),
        ).encode()
        signature = hmac.digest(self.key, raw, hashlib.sha256)
        return base64.urlsafe_b64encode(signature + raw).decode()

    def decode(self, cursor: str | None, scope: str, revision: str) -> int:
        if cursor is None:
            return 0
        try:
            blob = base64.b64decode(cursor, altchars=b"-_", validate=True)
            signature, raw = blob[:32], blob[32:]
            if not hmac.compare_digest(signature, hmac.digest(self.key, raw, hashlib.sha256)):
                raise ValueError("invalid signature")
            value: dict[str, Any] = json.loads(raw)
            if (
                value["scope"] != scope
                or value["revision"] != revision
                or value["expires"] <= time.time()
            ):
                raise ValueError("expired or mismatched cursor")
            offset = int(value["offset"])
            if offset < 0:
                raise ValueError("negative offset")
            return offset
        except (ValueError, KeyError, TypeError) as exc:
            raise RACPError(
                "CURSOR_EXPIRED",
                "cursor is invalid, expired, or directory changed",
                layer="provider",
            ) from exc
