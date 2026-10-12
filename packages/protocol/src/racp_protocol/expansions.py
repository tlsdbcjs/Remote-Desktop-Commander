"""Native capability expansion contracts; IDs are shared with the Rust dispatcher."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from racp_protocol.browser import BrowserFrame
from racp_protocol.models import Identifier, StrictModel
from racp_protocol.provider_models import FilePath, Sha256

Revision = Annotated[str, Field(min_length=1, max_length=128)]
Sid = Annotated[str, Field(pattern=r"^S-1-[0-9]+(?:-[0-9]+)+$", max_length=184)]


class ImagePreview(FilePath):
    max_source_bytes: int = Field(default=16777216, ge=1, le=16777216)
    max_pixels: int = Field(default=16777216, ge=1, le=16777216)
    width: int = Field(default=1024, ge=1, le=2048)
    height: int = Field(default=1024, ge=1, le=2048)
    expected_revision: Revision | None = None


class FileTrash(FilePath):
    expected_revision: Revision


class AclEntry(StrictModel):
    sid: Sid
    access: Literal["allow", "deny"] = "allow"
    rights: Literal["read", "read_write", "full"] = "read"
    inherit: bool = False


class AclModify(FilePath):
    expected_revision: Revision
    expected_acl_sha256: Sha256
    entries: list[AclEntry] = Field(min_length=1, max_length=64)
    inherit_parent: bool = True


class AgentDiagnostics(StrictModel):
    pass


class PerformanceSample(StrictModel):
    duration_ms: int = Field(default=1000, ge=100, le=30000)
    interval_ms: int = Field(default=250, ge=50, le=5000)


class EventQuery(StrictModel):
    channel: Literal["System", "Application", "Security"] = "System"
    after_record_id: int = Field(default=0, ge=0, le=9223372036854775807)
    limit: int = Field(default=100, ge=1, le=1000)


class DiagnosticExport(EventQuery):
    max_bytes: int = Field(default=1048576, ge=1024, le=16777216)


class BrowserScoped(BrowserFrame):
    expected_origin: str = Field(min_length=1, max_length=2048)
    navigation_revision: Revision


class BrowserLogRead(BrowserScoped):
    after_sequence: int = Field(default=0, ge=0, le=9223372036854775807)
    limit: int = Field(default=100, ge=1, le=256)


class BrowserStorageModify(BrowserScoped):
    storage: Literal["local", "session"] = "local"
    key: str = Field(min_length=1, max_length=2048)
    value: str | None = Field(default=None, max_length=32768)
    expected_value_sha256: Sha256


class JobTarget(StrictModel):
    target_operation_id: Identifier


class ServiceControl(StrictModel):
    name: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.-]+$")
    action: Literal["start", "stop", "pause", "continue"]
    expected_state: Literal["running", "stopped", "paused"]
    expected_config_sha256: Sha256


class ServiceConfigure(StrictModel):
    name: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_config_sha256: Sha256
    start_type: Literal["automatic", "manual", "disabled"]
    delayed_auto_start: bool = False


class RegistryRead(StrictModel):
    hive: Literal["current_user", "local_machine"] = "current_user"
    view: Literal["x64", "x86"] = "x64"
    key: str = Field(min_length=1, max_length=2048, pattern=r"^[^\x00]+$")
    name: str = Field(default="", max_length=256, pattern=r"^[^\x00]*$")


class RegistryData(StrictModel):
    type: Literal["string", "expand_string", "dword", "qword", "binary", "multi_string"]
    data_hex: str = Field(max_length=8192, pattern=r"^(?:[a-fA-F0-9]{2})*$")


class RegistryModify(RegistryRead):
    expected_value_sha256: Sha256
    value: RegistryData | None


class TaskRead(StrictModel):
    folder: str = Field(default="\\", min_length=1, max_length=1024)
    limit: int = Field(default=100, ge=1, le=1000)


class OwnedTaskModify(StrictModel):
    name: Identifier
    action: Literal["upsert", "delete"]
    expected_task_sha256: Sha256
    recipe: Literal["client_logon", "agent_logon"] = "client_logon"
    enabled: bool = True


class SoftwareInstall(FilePath):
    expected_sha256: Sha256
    expected_revision: Revision
    product_code: str = Field(pattern=r"^\{[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}\}$")


class SoftwareUninstall(StrictModel):
    product_code: str = Field(pattern=r"^\{[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}\}$")
    expected_ownership_sha256: Sha256


EXPANSION_MODELS: dict[str, type[BaseModel]] = {
    "filesystem.preview_image": ImagePreview,
    "filesystem.trash": FileTrash,
    "filesystem.acl_read": FilePath,
    "filesystem.acl_modify": AclModify,
    "diagnostics.agent": AgentDiagnostics,
    "performance.sample": PerformanceSample,
    "events.query": EventQuery,
    "diagnostics.export": DiagnosticExport,
    "browser.console_read": BrowserLogRead,
    "browser.network_read": BrowserLogRead,
    "browser.cookies_read": BrowserScoped,
    "browser.storage_modify": BrowserStorageModify,
    "jobs.observe": JobTarget,
    "jobs.cancel": JobTarget,
}

# Design drafts only. These operations are deliberately absent from Registry,
# generated schemas and permission bindings until their Providers are implemented.
DEFERRED_CONFIGURATION_MODELS: dict[str, type[BaseModel]] = {
    "services.control": ServiceControl,
    "services.configure": ServiceConfigure,
    "registry.read": RegistryRead,
    "registry.modify": RegistryModify,
    "tasks.read": TaskRead,
    "tasks.modify_owned": OwnedTaskModify,
    "software.install": SoftwareInstall,
    "software.uninstall": SoftwareUninstall,
}
EXPANSION_MUTATIONS = frozenset(
    {
        "filesystem.trash", "filesystem.acl_modify", "diagnostics.export", "browser.storage_modify",
        "jobs.cancel",
    }
)
