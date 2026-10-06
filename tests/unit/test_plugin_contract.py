import hashlib
import json
import sys
from pathlib import Path

import pytest
from racp_agent.plugins.manifest import (
    decode_frame,
    load_approved,
    validate_payload,
    validate_schema,
)
from racp_domain.models import RACPError
from racp_protocol.plugins import PluginCancel, PluginManifest, PluginResult


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"result","type":"event"}\n',
        b'{"number":NaN}\n',
        b'{"number":1e500}\n',
        b"\xff\n",
        b"{}",
        b"x" * (1024 * 1024 + 1),
    ],
    ids=["duplicate-field", "nan", "overflow", "utf8", "missing-newline", "oversized"],
)
def test_plugin_frames_reject_ambiguous_nonfinite_invalid_utf8_and_unbounded_data(
    raw: bytes,
) -> None:
    with pytest.raises(ValueError):
        decode_frame(raw)


def test_plugin_result_requires_exact_state_and_disallows_extra_fields() -> None:
    result = {"instance_id": "provider_fixture", "request_id": "req_fixture", "state": "FAILED"}
    with pytest.raises(ValueError):
        PluginResult.model_validate(result)
    with pytest.raises(ValueError):
        PluginResult.model_validate(
            {
                **result,
                "state": "SUCCEEDED",
                "result": {},
                "error": {"code": "ERROR", "message": "x"},
            }
        )
    with pytest.raises(ValueError):
        PluginResult.model_validate({**result, "state": "SUCCEEDED", "result": {}, "surprise": 1})


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_plugin_protocol_version_requires_the_exact_json_integer(version: object) -> None:
    with pytest.raises(ValueError):
        PluginCancel.model_validate(
            {
                "instance_id": "provider_fixture",
                "request_id": "req_fixture",
                "protocol_version": version,
            }
        )


def test_plugin_schema_never_fetches_remote_references(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: attempted.append(args))
    with pytest.raises(ValueError):
        validate_schema({"$ref": "https://owned-test.invalid/schema.json"})
    # Even a bypass of the local manifest check has no remote retrieval registry.
    with pytest.raises(RACPError):
        validate_payload({"$ref": "https://owned-test.invalid/schema.json"}, {})
    schema = {
        "$defs": {"value": {"type": "integer"}},
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/value"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    validate_schema(schema)
    validate_payload(schema, {"value": 42})
    assert not attempted


def test_manifest_hash_permissions_and_environment_are_locally_bound(tmp_path: Path) -> None:
    file = tmp_path / "plugin.json"
    value = {
        "name": "fixture",
        "version": "1.0.0",
        "backend_name": "fixture",
        "backend_version": "1",
        "capabilities": ["debugger"],
        "command": [str(Path(sys.executable).resolve())],
        "working_directory": str(tmp_path),
        "required_permissions": ["debugger.read"],
        "operations": [
            {
                "name": "debugger.registers",
                "capability": "debugger",
                "side_effect": False,
                "permission_scope": "debugger.read",
                "execution_modes": ["sync"],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        ],
    }
    PluginManifest.model_validate(value)
    file.write_text(json.dumps(value), encoding="utf-8")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises(PermissionError):
        load_approved(file, "0" * 64, frozenset({"debugger.read"}))
    with pytest.raises(PermissionError):
        load_approved(file, digest, frozenset())
    approved = load_approved(file, digest, frozenset({"debugger.read"}))
    assert approved.manifest.name == "fixture"
    value["environment"] = {"RACP_OWNER_TOKEN": "unapproved"}
    file.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RACPError) as protected:
        load_approved(
            file, hashlib.sha256(file.read_bytes()).hexdigest(), frozenset({"debugger.read"})
        )
    assert protected.value.error.code == "PERMISSION_DENIED"
