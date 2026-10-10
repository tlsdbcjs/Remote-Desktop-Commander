from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, ValidationError
from racp_domain.models import RACPError

from racp_protocol.browser import BROWSER_MODELS
from racp_protocol.clipboard import CLIPBOARD_MODELS
from racp_protocol.desktop import DESKTOP_MODELS, DESKTOP_READS
from racp_protocol.models import ShellInput
from racp_protocol.native_operations import NATIVE_MODELS
from racp_protocol.network_capture import NetworkCapture
from racp_protocol.os_observation import OS_OBSERVATION_MODELS, WINDOWS_INVENTORY_READS
from racp_protocol.provider_models import (
    SENSITIVE_PROCESS_READS,
    FileContentSearch,
    FileCopy,
    FileDelete,
    FileList,
    FileMkdir,
    FileMove,
    FilePatchBatch,
    FilePath,
    FileRead,
    FileSearch,
    FileWrite,
    ProcessDump,
    ProcessInspect,
    ProcessList,
    ProcessMemoryRead,
    ProcessMemoryRegions,
    ProcessSpawn,
    ProcessTarget,
    ProcessTerminate,
    ProcessWait,
)
from racp_protocol.proxy import PROXY_MODELS
from racp_protocol.reversing import RE_MODELS, RE_READS
from racp_protocol.terminal import (
    TerminalOpen,
    TerminalRead,
    TerminalResize,
    TerminalTarget,
    TerminalWrite,
)


@dataclass(frozen=True)
class OperationSpec:
    name: str
    mcp_name: str
    capability: str
    capability_version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    execution_modes: tuple[str, ...]
    side_effect: bool
    retry_class: str
    cancel: str
    max_timeout_ms: int
    resource_lock_key: str
    permission_scope: str
    minimum_os: tuple[str, ...]
    verification_ids: tuple[str, ...]
    default_timeout_ms: int = 30000
    max_sync_timeout_ms: int = 120000
    default_job_timeout_ms: int = 3600000
    max_job_timeout_ms: int = 86400000


SHELL = OperationSpec(
    "shell.exec",
    "shell_exec",
    "shell",
    "1.0.0",
    ShellInput.model_json_schema(),
    {
        "type": "object",
        "required": [
            "operation_id",
            "exit_code",
            "stdout",
            "stderr",
            "duration_ms",
            "truncated",
            "artifact_id",
            "termination_reason",
            "cleanup_status",
        ],
    },
    ("sync", "job"),
    True,
    "journal_only",
    "process_tree",
    86400000,
    "operation_id",
    "shell.execute",
    ("Windows", "Linux"),
    ("SHELL-01", "LIFE-01", "RPC-01", "RPC-02", "POLICY-01"),
    default_timeout_ms=60000,
)
REGISTRY = {SHELL.name: SHELL}
INPUT_MODELS: dict[str, type[BaseModel]] = {
    **PROXY_MODELS,
    **NATIVE_MODELS,
    **CLIPBOARD_MODELS,
    **OS_OBSERVATION_MODELS,
    **BROWSER_MODELS,
    **DESKTOP_MODELS,
    **RE_MODELS,
    "network.capture": NetworkCapture,
    "shell.exec": ShellInput,
    "filesystem.read": FileRead,
    "filesystem.write": FileWrite,
    "filesystem.stat": FilePath,
    "filesystem.list": FileList,
    "filesystem.mkdir": FileMkdir,
    "filesystem.copy": FileCopy,
    "filesystem.move": FileMove,
    "filesystem.delete": FileDelete,
    "filesystem.search": FileSearch,
    "filesystem.search_content": FileContentSearch,
    "filesystem.patch": FilePatchBatch,
    "filesystem.hash": FilePath,
    "process.list": ProcessList,
    "process.inspect": ProcessInspect,
    "process.spawn": ProcessSpawn,
    "process.terminate": ProcessTerminate,
    "process.wait": ProcessWait,
    "process.tree": ProcessTarget,
    "process.memory_regions": ProcessMemoryRegions,
    "process.memory_read": ProcessMemoryRead,
    "process.dump": ProcessDump,
    "terminal.open": TerminalOpen,
    "terminal.read": TerminalRead,
    "terminal.write": TerminalWrite,
    "terminal.resize": TerminalResize,
    "terminal.close": TerminalTarget,
    "terminal.keepalive": TerminalTarget,
}
MUTATIONS = frozenset(
    {
        *NATIVE_MODELS,
        *PROXY_MODELS,
        "shell.exec",
        "network.capture",
        "process.dump",
        "clipboard.write",
        "filesystem.write",
        "filesystem.patch",
        "filesystem.mkdir",
        "filesystem.copy",
        "filesystem.move",
        "filesystem.delete",
        "process.spawn",
        "process.terminate",
        "terminal.open",
        "terminal.write",
        "terminal.resize",
        "terminal.close",
        "terminal.keepalive",
        *[name for name in DESKTOP_MODELS if name not in DESKTOP_READS],
        *[name for name in RE_MODELS if name not in RE_READS],
        *[
            name
            for name in BROWSER_MODELS
            if name
            not in {"browser.pages", "browser.frames", "browser.snapshot", "browser.screenshot"}
        ],
    }
)
for name, model in INPUT_MODELS.items():
    if name == "shell.exec":
        continue
    capability, action = name.split(".")
    mutation = name in MUTATIONS
    REGISTRY[name] = OperationSpec(
        name,
        ("fs_" if capability == "filesystem" else capability + "_") + action,
        capability,
        "1.0.0",
        {"type": "object", **model.model_json_schema()},
        {"type": "object"},
        ("sync", "job"),
        mutation,
        "journal_only" if mutation else "read_safe",
        "cooperative_worker"
        if capability == "filesystem"
        else "owned_process_tree"
        if action == "spawn"
        else "wait_only"
        if action == "wait"
        else "owned_browser_context"
        if capability == "browser"
        else "session_input_release"
        if capability == "desktop"
        else "owned_plugin_resources"
        if capability in {"re", "debugger"}
        else "before_commit",
        86400000,
        "workspace"
        if capability == "filesystem"
        else "browser_id/page_id"
        if capability == "browser"
        else "session_id"
        if capability == "desktop"
        else "handle_id"
        if capability == "terminal"
        else "analysis_id/debug_id"
        if capability in {"re", "debugger"}
        else "pid_create_time_boot_id",
        "browser.evaluate"
        if name == "browser.evaluate"
        else "browser.attach"
        if name in {"browser.attach", "browser.cdp_targets"}
        else "process.memory.read"
        if name in SENSITIVE_PROCESS_READS
        else capability + (".mutate" if mutation else ".read"),
        ("Windows",)
        if capability == "desktop" or name in SENSITIVE_PROCESS_READS
        else ("Windows", "Linux"),
        ("FS-01", "RPC-01")
        if capability == "filesystem"
        else ("PTY-01", "LIFE-01", "STATE-01")
        if capability == "terminal"
        else ("BROWSER-01", "LIFE-01", "STATE-01")
        if capability == "browser"
        else ("DESK-01", "DESK-02", "LIFE-01", "STATE-01")
        if capability == "desktop"
        else ("RE-01", "LIFE-01", "STATE-01")
        if capability in {"re", "debugger"}
        else ("PROC-01", "LIFE-01"),
        default_timeout_ms=5000
        if name in {"filesystem.stat", "desktop.click", "desktop.type", "desktop.key"}
        else 10000
        if name in {"browser.click", "browser.type", "browser.key"}
        else 30000,
    )


REGISTRY["network.capture"] = replace(
    REGISTRY["network.capture"],
    minimum_os=("Windows",),
    cancel="owned_capture_process",
    max_timeout_ms=60000,
    max_sync_timeout_ms=60000,
    default_job_timeout_ms=60000,
    max_job_timeout_ms=60000,
    resource_lock_key="local_ip/local_port",
    permission_scope="network.capture",
    verification_ids=("CAPTURE-01", "LIFE-01", "RPC-01", "POLICY-01"),
)

REGISTRY["process.dump"] = replace(
    REGISTRY["process.dump"],
    minimum_os=("Windows",),
    cancel="owned_dump_process",
    max_timeout_ms=120000,
    max_sync_timeout_ms=120000,
    default_job_timeout_ms=120000,
    max_job_timeout_ms=120000,
    permission_scope="process.memory.dump",
    verification_ids=("DUMP-01", "LIFE-01", "RPC-01", "POLICY-01"),
)

for name in WINDOWS_INVENTORY_READS:
    REGISTRY[name] = replace(
        REGISTRY[name], minimum_os=("Windows",), resource_lock_key="os_account",
        verification_ids=("OS-INVENTORY-01", "RPC-01", "POLICY-01"),
    )

for name in CLIPBOARD_MODELS:
    REGISTRY[name] = replace(
        REGISTRY[name], minimum_os=("Windows",), cancel="session_broker_interrupt",
        resource_lock_key="session_id", max_timeout_ms=30000, max_sync_timeout_ms=30000,
        default_timeout_ms=5000, default_job_timeout_ms=30000, max_job_timeout_ms=30000,
        permission_scope="clipboard.text", verification_ids=("CLIPBOARD-01", "RPC-01", "POLICY-01"),
    )


for name in [*NATIVE_MODELS, *PROXY_MODELS]:
    REGISTRY[name] = replace(
        REGISTRY[name], minimum_os=("Windows",), cancel="owned_native_session",
        resource_lock_key="handle_id", max_timeout_ms=30000, max_sync_timeout_ms=30000,
        default_timeout_ms=10000, default_job_timeout_ms=30000, max_job_timeout_ms=30000,
        permission_scope="native.broad_os_access",
        verification_ids=("NATIVE-01", "LIFE-01", "POLICY-01"),
    )


def validate_payload(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation not in REGISTRY:
        raise RACPError("CAPABILITY_UNAVAILABLE", "operation is not implemented")
    try:
        value = INPUT_MODELS[operation].model_validate(payload).model_dump()
        if (
            operation in {"filesystem.copy", "filesystem.move"}
            and value.get("destination_workspace_id") is None
        ):
            value.pop("destination_workspace_id", None)
        return value
    except (ValidationError, LookupError) as exc:
        raise RACPError("INVALID_ARGUMENT", "invalid operation payload") from exc
