"""Permission identities and payload bindings must cover the real operation registry."""

import pytest
from racp_protocol.permissions import permission_catalog, required_permissions
from racp_protocol.registry import REGISTRY


def test_catalog_has_stable_unique_ids_and_explicit_implementation_status() -> None:
    catalog = permission_catalog()
    assert len(catalog) == 143
    assert len({p.id for p in catalog}) == 143
    assert len({p.category for p in catalog}) == 18
    assert all(p.label and p.description for p in catalog)
    assert {p.implementation for p in catalog} == {"rpc", "planned"}
    assert next(p for p in catalog if p.id == "network.capture.ipv4").implementation == "rpc"
    assert next(p for p in catalog if p.id == "storage.format").implementation == "planned"


def test_all_implemented_operations_have_known_permission_bindings() -> None:
    ids = {p.id for p in permission_catalog()}
    for operation in REGISTRY:
        required = required_permissions(operation, {})
        assert required and set(required) <= ids, operation
    with pytest.raises(ValueError, match="unknown operation"):
        required_permissions("network.future_unknown", {})


@pytest.mark.parametrize(
    ("operation", "payload", "expected"),
    [
        ("filesystem.write", {"mode": "create"}, {"files.create"}),
        ("filesystem.write", {"mode": "replace"}, {"files.edit"}),
        ("filesystem.read", {"binary": True}, {"files.read.binary", "artifacts.export"}),
        ("filesystem.copy", {}, {"files.copy", "files.read.binary", "files.create"}),
        (
            "shell.exec",
            {"mode": "shell", "env": {"SAFE_TEST": "x"}},
            {"exec.shell", "exec.environment.override"},
        ),
        ("process.spawn", {}, {"process.spawn", "exec.argv"}),
        ("terminal.open", {}, {"terminal.open", "exec.argv"}),
        ("desktop.key", {}, {"desktop.keyboard.keys"}),
        (
            "desktop.screenshot",
            {"window_id": "window_owned"},
            {"desktop.capture.window", "artifacts.export"},
        ),
        ("process.terminate", {"owned": True}, {"process.stop.external"}),
        ("debugger.command", {"action": "step_into"}, {"debugger.step"}),
        ("re.query", {"action": "decompile"}, {"analysis.binary.export"}),
    ],
)
def test_payload_requires_every_relevant_leaf(
    operation: str, payload: dict, expected: set[str]
) -> None:
    assert set(required_permissions(operation, payload)) == expected
