import json
import logging
import re
from collections import deque
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|token|secret|password|api[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"\b(authorization|cookie|token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)


def safe_text(value: str, limit: int = 65536) -> str:
    def replace_secret(match: re.Match[str]) -> str:
        prefix = match.group(1) if match.group(1) else match.group(2) + "="
        return prefix + "[REDACTED]"

    value = _SENSITIVE_TEXT.sub(replace_secret, value)
    visible: list[str] = []
    for char in value:
        code = ord(char)
        if char in "<>&":
            visible.append({"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}[char])
        elif code < 32 or code == 127:
            visible.append(f"\\x{code:02x}")
        else:
            visible.append(char)
        if sum(len(part) for part in visible) >= limit:
            break
    text = "".join(visible)
    return text[:limit]


def sanitize_fields(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:64]:
        if _SENSITIVE_KEY.search(key):
            result[key] = "[REDACTED]"
        elif isinstance(item, str):
            result[key] = safe_text(item, 4096)
        elif isinstance(item, (int, float, bool)) or item is None:
            result[key] = item
        elif isinstance(item, list):
            result[key] = [
                safe_text(entry, 1024) if isinstance(entry, str) else entry for entry in item[:64]
            ]
        else:
            result[key] = safe_text(str(item), 4096)
    return result


class BoundedLogQueue:
    def __init__(self, limit: int = 1000) -> None:
        if limit < 1:
            raise ValueError("log queue limit must be positive")
        self.items: deque[dict[str, Any]] = deque()
        self.limit = limit
        self.dropped = 0

    def put(self, item: dict[str, Any]) -> bool:
        if len(self.items) >= self.limit:
            self.dropped += 1
            return False
        self.items.append(item)
        return True

    def drain(self) -> list[dict[str, Any]]:
        values = list(self.items)
        self.items.clear()
        return values


def log_event(
    event: str,
    *,
    trace_id: str = "",
    request_id: str = "",
    device_id: str = "",
    operation: str = "",
    **safe_fields: Any,
) -> None:
    fields = sanitize_fields(safe_fields)
    logging.getLogger("racp").info(
        json.dumps(
            {
                "event": safe_text(event, 128),
                "timestamp": datetime.now(UTC).isoformat(),
                "trace_id": safe_text(trace_id, 128),
                "request_id": safe_text(request_id, 128),
                "device_id": safe_text(device_id, 256),
                "operation": safe_text(operation, 256),
                **fields,
            },
            ensure_ascii=False,
        )
    )
