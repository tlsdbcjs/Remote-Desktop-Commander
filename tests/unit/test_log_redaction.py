import json

from racp_observability.logging import BoundedLogQueue, safe_text, sanitize_fields


def test_sensitive_fields_control_bytes_and_html_are_neutralized() -> None:
    fields = sanitize_fields(
        {
            "authorization": "Bearer raw-secret",
            "cookie": "session=raw-cookie",
            "api_key": "raw-key",
            "safe": "<script>\x1b[31mhello\n",
        }
    )
    encoded = json.dumps(fields)
    assert "raw-secret" not in encoded and "raw-cookie" not in encoded and "raw-key" not in encoded
    assert "<script>" not in fields["safe"] and "\\x1b" in fields["safe"]
    assert len(safe_text("x" * 70000)) == 65536


def test_bounded_log_queue_reports_drop_count() -> None:
    queue = BoundedLogQueue(2)
    assert queue.put({"event": "one"})
    assert queue.put({"event": "two"})
    assert not queue.put({"event": "three"})
    assert queue.dropped == 1
    assert [item["event"] for item in queue.drain()] == ["one", "two"]
