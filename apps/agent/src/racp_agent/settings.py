"""Protected foreground Agent settings saved together with its Device credential."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from racp_protocol.models import Identifier, StrictModel
from racp_sdk.security import require_secure_url, tls_context

from racp_agent.providers.paths import PathGuard, is_link
from racp_agent.workspaces import WorkspacePaths, WorkspaceSpec


def default_state_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        if not base.is_absolute():
            base = Path.home() / "AppData/Local"
        return base / "RACP/agent"
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    if not base.is_absolute():
        base = Path.home() / ".local/state"
    return base / "racp/agent"


def local_path(path: Path) -> Path:
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise ValueError("Agent paths must be absolute local paths")
    for item in [path, *path.parents]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if is_link(info):
            raise PermissionError("Agent settings cannot follow links or reparse points")
    return Path(os.path.abspath(path))


def gateway_origin(value: str) -> str:
    require_secure_url(value)
    AnyHttpUrl(value)
    if urlsplit(value).path not in {"", "/"}:
        raise ValueError("Gateway URL must contain only the origin")
    return value.rstrip("/")


class StoredAgentSettings(StrictModel):
    version: int = Field(default=1, ge=1, le=1)
    gateway: str = Field(max_length=2048)
    device_id: Identifier
    workspace: Path
    allowed_workspaces: list[WorkspaceSpec] = Field(default_factory=list, max_length=15)
    data_dir: Path
    profile: Literal["read_only", "standard", "trusted_personal"] = "read_only"
    ca_file: Path | None = None
    desktop_enabled: bool = False

    @field_validator("gateway")
    @classmethod
    def fixed_gateway(cls, value: str) -> str:
        return gateway_origin(value)


class AgentSettings(StoredAgentSettings):
    @model_validator(mode="after")
    def local_settings(self) -> "AgentSettings":
        workspace = local_path(self.workspace)
        local_path(self.data_dir)
        PathGuard(workspace)
        WorkspacePaths(workspace, tuple(self.allowed_workspaces))
        if self.ca_file is not None:
            local_path(self.ca_file)
            tls_context(self.ca_file)
        return self


def stored_settings(credentials: dict[str, str]) -> StoredAgentSettings | None:
    document = credentials.get("agent_settings")
    if document is None:
        return None
    if len(document.encode("utf-8")) > 16384:
        raise ValueError("Agent settings exceed 16 KiB")
    settings = StoredAgentSettings.model_validate_json(
        document, context={"stored_settings_preview": True}
    )
    if (
        settings.gateway != gateway_origin(credentials["gateway"])
        or settings.device_id != credentials["device_id"]
    ):
        raise ValueError("Agent settings and credential identity differ")
    return settings


def saved_settings(credentials: dict[str, str]) -> AgentSettings | None:
    settings = stored_settings(credentials)
    return AgentSettings.model_validate(settings.model_dump()) if settings else None
