import pytest
from racp_domain.models import RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.registry import REGISTRY, validate_payload


@pytest.mark.parametrize(
    "operation,payload",
    [
        (
            "re.query",
            {"analysis_id": "analysis_fixture", "action": "functions", "address": "0x1000"},
        ),
        ("re.command", {"analysis_id": "analysis_fixture", "action": "raw", "command": "run"}),
        ("debugger.command", {"debug_id": "debug_fixture", "action": "continue", "symbol": "main"}),
        (
            "debugger.command",
            {"debug_id": "debug_fixture", "action": "set_breakpoint", "symbol": "main; run"},
        ),
        (
            "debugger.read_memory",
            {"debug_id": "debug_fixture", "address": "0xffffffffffffffff", "size_bytes": 2},
        ),
        (
            "debugger.read_memory",
            {"debug_id": "debug_fixture", "address": "0x1000", "size_bytes": 1024 * 1024 + 1},
        ),
        (
            "re.query",
            {
                "analysis_id": "analysis_fixture",
                "action": "xrefs",
                "address": "0x1",
                "address_kind": "module_rva",
            },
        ),
    ],
)
def test_re_tagged_actions_reject_opaque_commands_wrong_fields_and_address_overflow(
    operation: str, payload: dict[str, object]
) -> None:
    with pytest.raises(RACPError) as error:
        validate_payload(operation, payload)
    assert error.value.error.code == "INVALID_ARGUMENT"


def test_re_policy_separates_queries_mutations_and_registered_contracts() -> None:
    for operation in ("re.query", "debugger.registers", "debugger.wait", "debugger.read_memory"):
        assert not REGISTRY[operation].side_effect
        assert evaluate(operation, profile_rules("read_only")) == Decision.ALLOW
    for operation in (
        "re.open",
        "re.command",
        "debugger.launch",
        "debugger.command",
        "debugger.attach",
    ):
        assert REGISTRY[operation].side_effect
        assert evaluate(operation, profile_rules("read_only")) == Decision.DENY
        assert evaluate(operation, profile_rules("standard")) == Decision.REQUIRE_APPROVAL
    assert "oneOf" in REGISTRY["re.query"].input_schema
