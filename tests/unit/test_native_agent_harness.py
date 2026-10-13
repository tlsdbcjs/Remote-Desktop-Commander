"""Fragmented ConPTY output is complete only when the expected marker arrives."""

import httpx
import pytest

pytest.importorskip("msvcrt")
from scripts.test_native_agent import wait_for_terminal_marker  # noqa: E402


def reply(data: str, cursor: int, *, state: str = "SUCCEEDED", eof: bool = False) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "state": state,
            "result": {
                "data": data,
                "next_cursor": str(cursor),
                "eof": eof,
            },
        },
    )


def test_fragmented_marker_advances_byte_cursor_and_preserves_utf8() -> None:
    chunks = iter(["한글\r\n", "native-termi", "nal-output\r\n"])
    cursors: list[str] = []
    offset = 0

    def read(cursor: str, wait_ms: int, remaining: float) -> httpx.Response:
        nonlocal offset
        assert 0 < wait_ms <= 1000
        assert 0 < remaining <= 1
        assert int(cursor) == offset
        cursors.append(cursor)
        chunk = next(chunks)
        offset += len(chunk.encode("utf-8"))
        return reply(chunk, offset)

    output = wait_for_terminal_marker(read, "native-terminal-output", timeout=1)
    assert "native-terminal-output" in output
    assert output.startswith("한글")
    assert len(cursors) == 3


@pytest.mark.parametrize("response", [reply("", 0, state="FAILED"), reply("", 0, eof=True)])
def test_read_failure_or_eof_cannot_count_as_output(response: httpx.Response) -> None:
    with pytest.raises(AssertionError):
        wait_for_terminal_marker(lambda _cursor, _wait, _remaining: response, "marker", timeout=1)


def test_collection_stays_bounded_when_marker_never_arrives() -> None:
    with pytest.raises(TimeoutError):
        wait_for_terminal_marker(
            lambda _cursor, _wait, _remaining: reply("", 0), "marker", timeout=0
        )
    with pytest.raises(AssertionError, match="budget"):
        wait_for_terminal_marker(
            lambda _cursor, _wait, _remaining: reply("x" * (128 * 1024 + 1), 128 * 1024 + 1),
            "marker",
            timeout=1,
        )


def test_late_marker_cannot_pass_collection_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("scripts.test_native_agent.time.monotonic", lambda: clock[0])

    def read(_cursor: str, _wait_ms: int, _remaining: float) -> httpx.Response:
        clock[0] = 2.0
        return reply("marker", 6)

    with pytest.raises(TimeoutError):
        wait_for_terminal_marker(read, "marker", timeout=1)
