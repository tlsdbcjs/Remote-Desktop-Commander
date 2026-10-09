"""Local ceilings cannot be widened by payloads, profiles, or mutable settings."""

import json

import pytest
from pydantic import ValidationError
from racp_policy.engine import Decision
from racp_policy.permissions import LocalPermissions, compile_permissions, legacy_permissions


def test_missing_leaf_and_disabled_parent_deny_even_when_leaf_allows() -> None:
    settings = LocalPermissions(grants={"files.read.text": "allow"})
    snapshot = compile_permissions(settings)
    assert snapshot.decision("filesystem.read", {}) == Decision.ALLOW
    assert snapshot.decision("filesystem.delete", {}) == Decision.DENY
    blocked = settings.model_copy(update={"disabled_categories": ("files_read",)})
    assert compile_permissions(blocked).decision("filesystem.read", {}) == Decision.DENY


def test_approval_is_a_decision_not_a_caller_payload_override() -> None:
    snapshot = compile_permissions(LocalPermissions(grants={"files.read.text": "require_approval"}))
    assert snapshot.decision("filesystem.read", {"approved": True}) == Decision.REQUIRE_APPROVAL


@pytest.mark.parametrize(
    "document",
    [
        {"version": 9},
        {"grants": {"files.typo": "allow"}},
        {"grants": {"files.read.text": True}},
        {"grants": {"storage.format": "allow"}},
        {"managed_policy": {}},
        {"disabled_categories": ["unknown"]},
        {"constraints": {"strict_os_isolation": True}},
        {"constraints": {"max_timeout_ms": 0}},
    ],
)
def test_invalid_future_or_unenforceable_settings_are_rejected(document: dict) -> None:
    with pytest.raises(ValidationError):
        LocalPermissions.model_validate_json(json.dumps(document))


def test_compiled_snapshot_is_detached_and_revision_is_canonical() -> None:
    settings = LocalPermissions(grants={"files.read.text": "allow", "files.list": "deny"})
    snapshot = compile_permissions(settings)
    reverse = LocalPermissions(grants={"files.list": "deny", "files.read.text": "allow"})
    assert compile_permissions(reverse).revision == snapshot.revision
    settings.grants["files.read.text"] = "deny"
    assert snapshot.decision("filesystem.read", {}) == Decision.ALLOW
    assert compile_permissions(settings).revision != snapshot.revision


def test_workspaces_execution_and_budgets_are_ceilings() -> None:
    settings = LocalPermissions.model_validate_json("""{
      "grants":{"exec.argv":"allow","files.read.text":"allow"},
      "constraints":{"workspace_ids":["analysis"],"max_timeout_ms":2000,
        "max_output_bytes":4096,"executable_allowlist":["C:/Lab/tool.exe"],
        "executable_denylist":["C:/Lab/blocked.exe"]}}""")
    snapshot = compile_permissions(settings)
    assert (
        snapshot.decision(
            "shell.exec", {"argv": ["C:/Lab/tool.exe"]}, workspace_id="analysis", timeout_ms=2000
        )
        == Decision.ALLOW
    )
    assert (
        snapshot.decision("shell.exec", {"argv": ["tool.exe"]}, workspace_id="analysis")
        == Decision.DENY
    )
    assert snapshot.decision("filesystem.read", {}, workspace_id="default") == Decision.DENY
    assert (
        snapshot.decision("filesystem.read", {}, workspace_id="analysis", timeout_ms=2001)
        == Decision.DENY
    )
    assert (
        snapshot.decision("filesystem.read", {"max_bytes": 4097}, workspace_id="analysis")
        == Decision.DENY
    )


def test_legacy_migration_preserves_desktop_off_and_never_enables_future_items() -> None:
    off = legacy_permissions(desktop_enabled=False)
    on = legacy_permissions(desktop_enabled=True)
    assert off.grants["desktop.keyboard.keys"] == "deny"
    assert on.grants["desktop.keyboard.keys"] == "allow"
    assert off.grants["files.read.text"] == "allow"
    assert "storage.format" not in off.grants
    assert "network.capture.ipv4" not in off.grants


def test_future_rpc_addition_does_not_expand_v1_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    import racp_policy.permissions as policies

    current = policies.permission_catalog()
    future = current[0].model_copy(
        update={"id": "system.future", "implementation": "rpc", "operations": ("system.future",)}
    )
    monkeypatch.setattr(policies, "permission_catalog", lambda: (*current, future))
    assert "system.future" not in policies.legacy_permissions(desktop_enabled=True).grants
