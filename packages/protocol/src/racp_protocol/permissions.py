"""Stable local permission identities and payload-dependent operation bindings.

Descriptor metadata is shared with the Client. Authorization always uses the
bindings below; a descriptor listing a primitive is not itself an allow rule.
"""

from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Literal

from pydantic import ConfigDict, TypeAdapter

from racp_protocol.models import StrictModel


class PermissionDescriptor(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str
    category: str
    category_label: str
    label: str
    description: str
    baseline: str
    implementation: Literal["rpc", "cli", "planned"]
    operations: tuple[str, ...]


OPERATION_PERMISSIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "system.info": ("system.identity.read",),
        "system.resources": ("system.resources.read",),
        "system.locale": ("system.locale.read",),
        "system.environment": ("system.environment.read",),
        "network.interfaces": ("network.interfaces.read",),
        "network.connections": ("network.connections.read",),
        "network.capture": ("network.capture.ipv4", "artifacts.export"),
        "process.dump": ("memory.dump.create", "artifacts.export"),
        "native.prepare": ("network.tunnel.open", "exec.argv", "debugger.attach",
                           "memory.bytes.read", "artifacts.export"),
        "native.start": ("network.tunnel.open", "exec.argv", "debugger.attach",
                         "memory.bytes.read", "artifacts.export"),
        "native.close": ("network.tunnel.open", "exec.argv"),
        "proxy.prepare": ("network.proxy.intercept", "network.http.replay", "network.tunnel.open",
                          "exec.argv", "artifacts.export"),
        "proxy.close": ("network.tunnel.open", "exec.argv"),
        "clipboard.read": ("clipboard.text.read",),
        "clipboard.state": ("clipboard.text.write",),
        "clipboard.write": ("clipboard.text.write",),
        "storage.volumes": ("storage.volumes.read",),
        "services.list": ("services.read",),
        "services.get": ("services.read",),
        "software.inventory": ("software.inventory.read",),
        "shell.exec": ("exec.argv",),
        "browser.open": ("browser.create",),
        "browser.attach": ("browser.attach.external",),
        "browser.cdp_targets": ("browser.attach.external",),
        "browser.pages": ("browser.pages.read",),
        "browser.new_page": ("browser.create",),
        "browser.close": ("browser.close",),
        "browser.keepalive": ("browser.create",),
        "browser.close_page": ("browser.close",),
        "browser.navigate": ("browser.navigate",),
        "browser.snapshot": ("browser.pages.read",),
        "browser.frames": ("browser.pages.read",),
        "browser.screenshot": ("browser.capture", "artifacts.export"),
        "browser.click": ("browser.input",),
        "browser.type": ("browser.input",),
        "browser.key": ("browser.input",),
        "browser.evaluate": ("browser.evaluate",),
        "browser.download": ("browser.files.download", "artifacts.export"),
        "browser.upload": ("browser.files.upload", "artifacts.import"),
        "desktop.sessions": ("system.sessions.read",),
        "desktop.monitors": ("desktop.monitors.read",),
        "desktop.foreground": ("desktop.windows.read",),
        "desktop.windows": ("desktop.windows.read",),
        "desktop.inspect": ("desktop.windows.read", "desktop.uia.read"),
        "desktop.screenshot": ("desktop.capture.screen", "artifacts.export"),
        "desktop.lease_acquire": ("desktop.windows.read",),
        "desktop.lease_renew": ("desktop.windows.read",),
        "desktop.lease_release": ("desktop.windows.read",),
        "desktop.activate": ("desktop.windows.read", "desktop.mouse.move"),
        "desktop.move": ("desktop.mouse.move",),
        "desktop.click": ("desktop.mouse.click",),
        "desktop.type": ("desktop.keyboard.text",),
        "desktop.key": ("desktop.keyboard.keys",),
        "desktop.scroll": ("desktop.mouse.scroll",),
        "desktop.drag": ("desktop.mouse.drag",),
        "desktop.invoke": ("desktop.uia.invoke",),
        "desktop.set_value": ("desktop.uia.set_value",),
        "re.backends": ("system.identity.read",),
        "re.open": ("analysis.binary.export", "files.read.binary"),
        "re.query": ("analysis.binary.export",),
        "re.command": ("analysis.binary.export", "files.edit"),
        "re.close": ("analysis.binary.export",),
        "re.keepalive": ("analysis.binary.export",),
        "debugger.backends": ("system.identity.read",),
        "debugger.launch": ("debugger.attach", "exec.argv", "process.spawn"),
        "debugger.attach": ("debugger.attach",),
        "debugger.command": ("debugger.breakpoint",),
        "debugger.info": ("debugger.observe",),
        "debugger.registers": ("debugger.observe",),
        "debugger.read_memory": ("debugger.observe", "memory.bytes.read"),
        "debugger.backtrace": ("debugger.observe",),
        "debugger.wait": ("debugger.observe",),
        "debugger.close": ("debugger.detach",),
        "debugger.keepalive": ("debugger.attach",),
        "filesystem.read": ("files.read.text",),
        "filesystem.write": ("files.create",),
        "filesystem.stat": ("files.metadata.read",),
        "filesystem.list": ("files.list",),
        "filesystem.mkdir": ("files.directory.create",),
        "filesystem.copy": ("files.copy", "files.read.binary", "files.create"),
        "filesystem.move": ("files.move", "files.create"),
        "filesystem.delete": ("files.delete",),
        "filesystem.search": ("files.search.names",),
        "filesystem.search_content": ("files.search.content", "files.read.text"),
        "filesystem.patch": ("files.patch", "files.edit", "files.read.text"),
        "filesystem.hash": ("files.hash",),
        "process.list": ("process.list",),
        "process.inspect": ("process.inspect",),
        "process.spawn": ("process.spawn", "exec.argv"),
        "process.terminate": ("process.stop.external",),
        "process.wait": ("process.wait",),
        "process.tree": ("process.tree",),
        "process.memory_regions": ("memory.regions.read",),
        "process.memory_read": ("memory.bytes.read",),
        "terminal.open": ("terminal.open", "exec.argv"),
        "terminal.read": ("terminal.output.read",),
        "terminal.write": ("terminal.input.write", "exec.argv"),
        "terminal.resize": ("terminal.resize",),
        "terminal.close": ("terminal.close",),
        "terminal.keepalive": ("terminal.open",),
    }
)

# Additional capabilities of existing primitives: conditional input gates or
# output fields. They are not unconditionally required for metadata-only reads.
CONDITIONAL_PERMISSION_OPERATIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "exec.shell": ("shell.exec",),
        "exec.environment.override": ("shell.exec", "process.spawn", "terminal.open"),
        "files.edit": ("filesystem.write", "filesystem.copy", "filesystem.move"),
        "files.read.binary": ("filesystem.read",),
        "artifacts.export": ("filesystem.read", "process.memory_read"),
        "artifacts.import": ("filesystem.write",),
        "desktop.capture.window": ("desktop.screenshot",),
        "debugger.step": ("debugger.command",),
        "debugger.resume": ("debugger.command",),
        "process.stop.owned": ("process.terminate",),
        "process.arguments.read": ("process.inspect", "process.tree", "process.list"),
    }
)


def permission_operations(permission_id: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {op for op, ids in OPERATION_PERMISSIONS.items() if permission_id in ids}
            | set(CONDITIONAL_PERMISSION_OPERATIONS.get(permission_id, ()))
        )
    )


@lru_cache(maxsize=1)
def permission_catalog() -> tuple[PermissionDescriptor, ...]:
    raw = files("racp_protocol").joinpath("permission_catalog.json").read_text(encoding="utf-8")
    catalog = TypeAdapter(tuple[PermissionDescriptor, ...]).validate_json(raw)
    if len({item.id for item in catalog}) != len(catalog):
        raise ValueError("duplicate permission ID")
    return catalog


def required_permissions(
    operation: str, payload: Mapping[str, Any], *, owned: bool = False
) -> tuple[str, ...]:
    if operation not in OPERATION_PERMISSIONS:
        raise ValueError("unknown operation")
    required = set(OPERATION_PERMISSIONS[operation])
    if operation == "shell.exec":
        required = {"exec.shell" if payload.get("mode") == "shell" else "exec.argv"}
    elif operation == "filesystem.read" and payload.get("binary"):
        required = {"files.read.binary", "artifacts.export"}
    elif operation == "filesystem.write":
        required = {"files.create" if payload.get("mode", "create") == "create" else "files.edit"}
        if payload.get("artifact_id"):
            required.add("artifacts.import")
    elif operation in {"filesystem.copy", "filesystem.move"} and payload.get("overwrite"):
        required.discard("files.create")
        required.add("files.edit")
    elif operation == "process.terminate" and owned:
        # Ownership is a verified Provider fact, never a user payload flag.
        required = {"process.stop.owned"}
    elif operation == "desktop.screenshot" and payload.get("window_id"):
        required = {"desktop.capture.window", "artifacts.export"}
    elif operation == "process.memory_read" and payload.get("size_bytes", 4096) > 4096:
        required.add("artifacts.export")
    elif operation == "debugger.command":
        action = payload.get("action")
        if action in {"step_into", "step_over"}:
            required = {"debugger.step"}
        elif action in {"continue", "interrupt"}:
            required = {"debugger.resume"}
    if operation in {"shell.exec", "process.spawn", "terminal.open"} and payload.get("env"):
        required.add("exec.environment.override")
    return tuple(sorted(required))
