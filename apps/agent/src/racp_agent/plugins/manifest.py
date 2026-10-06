import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from racp_domain.models import RACPError
from racp_protocol.models import MAX_MESSAGE_BYTES, validate_tree
from racp_protocol.plugins import PLUGIN_MESSAGE, PluginManifest, PluginMessage
from referencing import Registry

from racp_agent.providers.paths import is_link
from racp_agent.providers.shell import execution_env


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate plugin JSON field")
        value[key] = item
    return value


def finite_tree(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite plugin JSON number")
    if isinstance(value, dict):
        for item in value.values():
            finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            finite_tree(item)


def decode_json(raw: bytes, limit: int = MAX_MESSAGE_BYTES) -> Any:
    if not 0 < len(raw) <= limit:
        raise ValueError("plugin JSON byte limit exceeded")
    value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique_object)
    validate_tree(value)
    finite_tree(value)
    return value


def encode_frame(message: PluginMessage) -> bytes:
    raw = (message.model_dump_json() + "\n").encode()
    decode_json(raw)
    return raw


def decode_frame(raw: bytes) -> PluginMessage:
    if not raw.endswith(b"\n"):
        raise ValueError("unterminated plugin JSON line")
    return PLUGIN_MESSAGE.validate_python(decode_json(raw))


def validate_schema(schema: dict[str, Any]) -> None:
    if len(json.dumps(schema, allow_nan=False).encode()) > 16384:
        raise ValueError("plugin operation schema exceeds 16 KiB")
    validate_tree(schema)

    # Local references are allowed; resolution never downloads a remote schema.
    def references(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#/")
                ):
                    raise ValueError("plugin schema reference must be local")
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(schema)
    Draft202012Validator.check_schema(schema)


@dataclass(frozen=True)
class ApprovedPlugin:
    manifest: PluginManifest
    sha256: str


def load_approved(path: Path, sha256: str, permissions: frozenset[str]) -> ApprovedPlugin:
    if not path.is_absolute() or any(is_link(parent.lstat()) for parent in [path, *path.parents]):
        raise PermissionError("plugin manifest must use an explicit local file without links")
    with path.open("rb") as stream:
        raw = stream.read(256 * 1024 + 1)
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise PermissionError("plugin manifest differs from local allowlist hash")
    manifest = PluginManifest.model_validate(decode_json(raw, 256 * 1024))
    if not set(manifest.required_permissions) <= permissions:
        raise PermissionError("plugin permissions are not locally approved")
    executable, cwd = Path(manifest.command[0]), Path(manifest.working_directory)
    if not executable.is_absolute() or not executable.is_file() or is_link(executable.lstat()):
        raise ValueError("plugin executable must be an installed absolute file")
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        raise ValueError("plugin command cannot invoke an implicit batch shell")
    if not cwd.is_absolute() or not cwd.is_dir() or is_link(cwd.lstat()):
        raise ValueError("plugin working directory must be an explicit local directory")
    execution_env(manifest.environment)
    for operation in manifest.operations:
        validate_schema(operation.input_schema)
        validate_schema(operation.output_schema)
    return ApprovedPlugin(manifest, sha256)


def validate_payload(schema: dict[str, Any], value: dict[str, Any]) -> None:
    try:
        validate_tree(value)
        finite_tree(value)
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except Exception as exc:
        raise RACPError(
            "INVALID_ARGUMENT", "plugin schema validation failed", layer="plugin"
        ) from exc
