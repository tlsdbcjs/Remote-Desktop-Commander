"""Local permission ceilings; no managed-policy transport or caller overrides."""

import hashlib
import json
import ntpath
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from racp_protocol.models import StrictModel, WorkspaceId
from racp_protocol.permissions import permission_catalog, required_permissions

from racp_policy.engine import Decision

Grant = Literal["allow", "deny", "require_approval"]

# Immutable migration baseline: adding an RPC must never grant it to v1 devices.
LEGACY_V1_IDS = frozenset(
    {
        "system.identity.read",
        "system.sessions.read",
        "files.list",
        "files.search.names",
        "files.read.text",
        "files.read.binary",
        "files.metadata.read",
        "files.hash",
        "files.create",
        "files.directory.create",
        "files.edit",
        "files.copy",
        "files.move",
        "files.delete",
        "process.list",
        "process.inspect",
        "process.arguments.read",
        "process.tree",
        "process.wait",
        "process.spawn",
        "process.stop.owned",
        "process.stop.external",
        "exec.argv",
        "exec.shell",
        "exec.environment.override",
        "terminal.open",
        "terminal.output.read",
        "terminal.input.write",
        "terminal.resize",
        "terminal.close",
        "desktop.monitors.read",
        "desktop.windows.read",
        "desktop.capture.screen",
        "desktop.capture.window",
        "desktop.uia.read",
        "desktop.mouse.click",
        "desktop.mouse.move",
        "desktop.mouse.drag",
        "desktop.mouse.scroll",
        "desktop.keyboard.text",
        "desktop.keyboard.keys",
        "desktop.uia.invoke",
        "desktop.uia.set_value",
        "browser.create",
        "browser.close",
        "browser.pages.read",
        "browser.capture",
        "browser.navigate",
        "browser.input",
        "browser.evaluate",
        "browser.files.upload",
        "browser.files.download",
        "browser.attach.external",
        "memory.regions.read",
        "memory.bytes.read",
        "analysis.binary.export",
        "debugger.attach",
        "debugger.detach",
        "debugger.observe",
        "debugger.breakpoint",
        "debugger.step",
        "debugger.resume",
        "artifacts.export",
        "artifacts.import",
    }
)


class PermissionConstraints(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    workspace_ids: tuple[WorkspaceId, ...] = Field(default=(), max_length=16)
    max_timeout_ms: int = Field(default=86400000, ge=1, le=86400000)
    max_output_bytes: int = Field(default=1024**3, ge=1, le=1024**3)
    executable_allowlist: tuple[str, ...] = Field(default=(), max_length=128)
    executable_denylist: tuple[str, ...] = Field(default=(), max_length=128)
    strict_os_isolation: Literal[False] = False

    @model_validator(mode="after")
    def executable_paths(self) -> "PermissionConstraints":
        for path in (*self.executable_allowlist, *self.executable_denylist):
            if not path or len(path) > 4096 or "\0" in path or not ntpath.isabs(path):
                raise ValueError("Execution rules require absolute executable paths")
        return self


class LocalPermissions(StrictModel):
    version: Literal[1] = 1
    grants: dict[str, Grant] = Field(default_factory=dict, max_length=256)
    disabled_categories: tuple[str, ...] = Field(default=(), max_length=18)
    constraints: PermissionConstraints = Field(default_factory=PermissionConstraints)

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("Permission schema version must be an integer")
        return value

    @model_validator(mode="after")
    def known_permissions(self) -> "LocalPermissions":
        catalog = {item.id: item for item in permission_catalog()}
        categories = {item.category for item in catalog.values()}
        if set(self.disabled_categories) - categories:
            raise ValueError("Unknown permission category")
        for identity, grant in self.grants.items():
            if identity not in catalog:
                raise ValueError("Unknown permission ID")
            if grant != "deny" and catalog[identity].implementation != "rpc":
                raise ValueError("Permission has no enforceable RPC implementation")
        return self


def _executable(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


@dataclass(frozen=True)
class PermissionSnapshot:
    revision: str
    grants: tuple[tuple[str, Grant], ...]
    disabled_categories: frozenset[str]
    categories: tuple[tuple[str, str], ...]
    constraints: PermissionConstraints

    def leaf(self, identity: str) -> Decision:
        if dict(self.categories).get(identity) in self.disabled_categories:
            return Decision.DENY
        return Decision(dict(self.grants).get(identity, "deny"))

    def decision(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        workspace_id: str = "default",
        timeout_ms: int = 1,
        owned: bool = False,
    ) -> Decision:
        try:
            required = required_permissions(operation, payload, owned=owned)
        except ValueError:
            return Decision.DENY
        ceiling = self.constraints
        if ceiling.workspace_ids and workspace_id not in ceiling.workspace_ids:
            return Decision.DENY
        # Copy/move must also satisfy the destination workspace ceiling.
        destination = payload.get("destination_workspace_id") or workspace_id
        if ceiling.workspace_ids and destination not in ceiling.workspace_ids:
            return Decision.DENY
        if timeout_ms > ceiling.max_timeout_ms:
            return Decision.DENY
        for key in ("max_bytes", "max_output_bytes", "size_bytes"):
            size = payload.get(key)
            if isinstance(size, int) and size > ceiling.max_output_bytes:
                return Decision.DENY
        if operation.startswith(("native.", "proxy.")) and (
            ceiling.executable_allowlist or ceiling.executable_denylist
        ):
            # Opaque native commands can create/evaluate target code. An executable
            # list cannot bound them; require the explicit broad execution grant.
            return Decision.DENY
        if operation in {"native.prepare", "proxy.prepare"} and (
            payload.get("max_bytes", 8 * 1024**2) > ceiling.max_output_bytes
            or payload.get("lease_seconds", 120) * 1000 > ceiling.max_timeout_ms
        ):
            return Decision.DENY
        if operation in {"shell.exec", "process.spawn", "terminal.open", "debugger.launch"}:
            allow, deny = ceiling.executable_allowlist, ceiling.executable_denylist
            if allow or deny:
                argv = (
                    [payload["executable"]]
                    if operation == "debugger.launch"
                    else payload.get("argv")
                )
                # A command string is not an executable identity. Rules cannot
                # constrain its arbitrary children; reject instead of guessing.
                if payload.get("mode") == "shell" or not argv or not ntpath.isabs(argv[0]):
                    return Decision.DENY
                selected = _executable(argv[0])
                if selected in {_executable(path) for path in deny}:
                    return Decision.DENY
                if allow and selected not in {_executable(path) for path in allow}:
                    return Decision.DENY
        decisions = {self.leaf(identity) for identity in required}
        if Decision.DENY in decisions:
            return Decision.DENY
        if Decision.REQUIRE_APPROVAL in decisions:
            return Decision.REQUIRE_APPROVAL
        return Decision.ALLOW


def compile_permissions(settings: LocalPermissions) -> PermissionSnapshot:
    # Validate again: frozen outer models alone do not make nested dictionaries immutable.
    validated = LocalPermissions.model_validate_json(settings.model_dump_json())
    document = validated.model_dump(mode="json")
    document["disabled_categories"] = sorted(set(validated.disabled_categories))
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return PermissionSnapshot(
        revision=hashlib.sha256(canonical.encode()).hexdigest(),
        grants=tuple(sorted(validated.grants.items())),
        disabled_categories=frozenset(validated.disabled_categories),
        categories=tuple((item.id, item.category) for item in permission_catalog()),
        constraints=validated.constraints,
    )


def legacy_permissions(*, desktop_enabled: bool) -> LocalPermissions:
    """Freeze the pre-v2 RPC baseline. Future descriptors remain default-denied."""
    return LocalPermissions(
        grants={
            item.id: "deny"
            if not desktop_enabled and item.category in {"desktop_read", "desktop_input"}
            else "allow"
            for item in permission_catalog()
            if item.id in LEGACY_V1_IDS
        }
    )
