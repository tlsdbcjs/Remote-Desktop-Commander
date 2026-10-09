import pytest
from racp_domain.models import RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.permissions import required_permissions
from racp_protocol.registry import REGISTRY, validate_payload


def test_clipboard_text_has_independent_rights_and_explicit_session() -> None:
    read = validate_payload("clipboard.read", {"session_id": 1})
    write = validate_payload(
        "clipboard.write", {"session_id": 1, "text": "안녕", "expected_sequence": 17}
    )
    assert read["max_bytes"] == 4096
    assert required_permissions("clipboard.read", read) == ("clipboard.text.read",)
    assert required_permissions("clipboard.write", write) == ("clipboard.text.write",)
    assert REGISTRY["clipboard.read"].minimum_os == ("Windows",)
    assert REGISTRY["clipboard.write"].retry_class == "journal_only"
    assert evaluate("clipboard.read", profile_rules("read_only")) == Decision.ALLOW
    assert evaluate("clipboard.write", profile_rules("read_only")) == Decision.DENY
    assert evaluate("clipboard.write", profile_rules("standard")) == Decision.REQUIRE_APPROVAL
    assert evaluate("clipboard.write", profile_rules("trusted_personal")) == Decision.ALLOW


def test_write_only_permission_can_query_sequence_without_clipboard_text() -> None:
    data = validate_payload("clipboard.state", {"session_id": 1})
    assert required_permissions("clipboard.state", data) == ("clipboard.text.write",)
    assert evaluate("clipboard.state", profile_rules("read_only")) == Decision.ALLOW


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("clipboard.read", {}),
        ("clipboard.read", {"session_id": 0}),
        ("clipboard.read", {"session_id": True}),
        ("clipboard.read", {"session_id": 1, "max_bytes": 8193}),
        ("clipboard.write", {"session_id": 1, "text": "missing-revision"}),
        ("clipboard.write", {"session_id": 1, "text": "embedded\0NUL", "expected_sequence": 1}),
        ("clipboard.write", {"session_id": 1, "text": "x" * 8193, "expected_sequence": 1}),
        ("clipboard.write", {"session_id": 1, "text": "한" * 3000, "expected_sequence": 1}),
        (
            "clipboard.write",
            {"session_id": 1, "clear": True, "text": "ambiguous", "expected_sequence": 1},
        ),
        ("clipboard.write", {"session_id": 1, "expected_sequence": True, "text": "x"}),
        ("clipboard.write", {"session_id": 1, "expected_sequence": 1}),
    ],
)
def test_clipboard_rejects_missing_identity_revision_and_unbounded_text(
    operation: str, payload: dict
) -> None:
    with pytest.raises(RACPError) as raised:
        validate_payload(operation, payload)
    assert raised.value.error.code == "INVALID_ARGUMENT"
