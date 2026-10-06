import pytest
from racp_agent.providers.terminal_buffer import TerminalBuffer
from racp_domain.models import RACPError


def test_utf8_boundaries_and_independent_byte_cursors() -> None:
    ring = TerminalBuffer()
    text = "A한글🙂끝"
    raw = text.encode()
    ring.append(raw[:3])
    first = ring.read(0, 65536, eof=False)
    assert first["data"] == "A" and first["next_cursor"] == "1"
    ring.append(raw[3:])
    second = ring.read(1, 65536, eof=False)
    assert first["data"] + second["data"] == text
    assert ring.read(0, 65536, eof=False)["data"] == text
    tiny = ring.read(1, 1, eof=False)
    assert tiny["next_cursor"] == "1" and tiny["required_min_bytes"] == 4
    assert second["next_cursor"] == str(len(raw))


def test_ring_overflow_requires_explicit_new_cursor_and_counts_invalid_bytes() -> None:
    ring = TerminalBuffer(8)
    ring.append(b"0123456789")
    with pytest.raises(RACPError) as expired:
        ring.read(0, 8, eof=False)
    assert expired.value.error.code == "CURSOR_EXPIRED"
    assert expired.value.error.details == {"earliest_cursor": "2", "lost_bytes": "2"}
    assert ring.read(2, 8, eof=False)["data"] == "23456789"
    ring.append("\ufffd".encode() + b"\xff\xe2")
    result = ring.read(10, 8, eof=True)
    assert result["data"] == "\ufffd\ufffd\ufffd"
    assert result["invalid_byte_replacements"] == 2
    assert result["next_cursor"] == "15" and result["eof"]
