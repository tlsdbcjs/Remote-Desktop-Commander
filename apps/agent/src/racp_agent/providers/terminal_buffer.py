import codecs
from contextvars import ContextVar
from typing import Any

from racp_domain.models import RACPError

_replacements: ContextVar[int] = ContextVar("racp_terminal_replacements", default=0)


def _replace(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeDecodeError):
        raise error
    _replacements.set(_replacements.get() + 1)
    return "\ufffd", error.end


codecs.register_error("racp_terminal_replace", _replace)


class TerminalBuffer:
    """Bounded raw-byte history. Each consumer owns its own monotonic cursor."""

    def __init__(self, capacity: int = 4 * 1024 * 1024) -> None:
        self.capacity = capacity
        self.data = bytearray()
        self.earliest = 0
        self.end = 0

    def append(self, block: bytes) -> None:
        self.end += len(block)
        self.data.extend(block[-self.capacity :])
        if len(self.data) > self.capacity:
            del self.data[: len(self.data) - self.capacity]
        self.earliest = self.end - len(self.data)

    def read(self, cursor: int, max_bytes: int, *, eof: bool) -> dict[str, Any]:
        if cursor < self.earliest:
            raise RACPError(
                "CURSOR_EXPIRED",
                "terminal bytes were evicted",
                layer="provider",
                earliest_cursor=str(self.earliest),
                lost_bytes=str(self.earliest - cursor),
            )
        if cursor > self.end:
            raise RACPError("INVALID_ARGUMENT", "cursor exceeds terminal output", layer="provider")
        start = cursor - self.earliest
        raw = bytes(self.data[start : start + max_bytes])
        decoder = codecs.getincrementaldecoder("utf-8")("racp_terminal_replace")
        final = eof and cursor + len(raw) == self.end
        token = _replacements.set(0)
        try:
            text = decoder.decode(raw, final=final)
            invalid = _replacements.get()
        finally:
            _replacements.reset(token)
        pending = decoder.getstate()[0]
        consumed = raw[: len(raw) - len(pending)]
        return {
            "data": text,
            "next_cursor": str(cursor + len(consumed)),
            "earliest_cursor": str(self.earliest),
            "lost_bytes": "0",
            "eof": eof and cursor + len(consumed) == self.end,
            "invalid_byte_replacements": invalid,
            "required_min_bytes": 4 if pending and not consumed else None,
        }
