import pytest
from racp_domain.models import RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.permissions import required_permissions
from racp_protocol.registry import REGISTRY, validate_payload


def valid() -> dict:
    return {"pid": 1234, "create_time": 1790000000.0, "agent_boot_id": "boot_owned"}


def test_dump_is_sensitive_windows_recipe_with_separate_export_permission() -> None:
    data = validate_payload("process.dump", valid())
    assert data["mode"] == "mini" and data["max_bytes"] == 64 * 1024**2
    assert set(required_permissions("process.dump", data)) == {
        "memory.dump.create",
        "artifacts.export",
    }
    assert REGISTRY["process.dump"].minimum_os == ("Windows",)
    assert (
        REGISTRY["process.dump"].side_effect
        and REGISTRY["process.dump"].retry_class == "journal_only"
    )
    assert evaluate("process.dump", profile_rules("read_only")) == Decision.DENY
    assert evaluate("process.dump", profile_rules("standard")) == Decision.REQUIRE_APPROVAL
    assert evaluate("process.dump", profile_rules("trusted_personal")) == Decision.ALLOW


@pytest.mark.parametrize(
    "extra",
    [
        {"pid": True},
        {"pid": 0},
        {"create_time": 0.0},
        {"create_time": float("inf")},
        {"mode": "kernel"},
        {"max_bytes": 4095},
        {"max_bytes": 256 * 1024**2 + 1},
        {"output": "C:/arbitrary.dmp"},
        {"flags": 0xFFFFFFFF},
        {"command": "cmd.exe"},
    ],
)
def test_dump_rejects_unscoped_unbounded_or_caller_recipe_inputs(extra: dict) -> None:
    with pytest.raises(RACPError) as raised:
        validate_payload("process.dump", {**valid(), **extra})
    assert raised.value.error.code == "INVALID_ARGUMENT"
