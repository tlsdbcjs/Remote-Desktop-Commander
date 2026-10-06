"""Versioned service configuration; OS identity is checked before credentials are opened."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from racp_protocol.models import StrictModel

from racp_agent.broker.identity import Identity
from racp_agent.broker.login_registration import LoginEndpoint
from racp_agent.workspaces import WorkspacePaths, WorkspaceSpec


class ServiceConfig(StrictModel):
    version: int = Field(default=1, ge=1, le=1)
    service_name: str = Field(default="RACPAgent", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    service_sid: str = Field(pattern=r"^S-1-5-80-(?:\d+-){4}\d+$", max_length=184)
    agent_sid: str = Field(pattern=r"^S-1-(?:\d+-)*\d+$", max_length=184)
    credentials: Path
    data_dir: Path
    workspace: Path
    allowed_workspaces: list[WorkspaceSpec] = Field(default_factory=list, max_length=15)
    profile: Literal["read_only", "standard", "trusted_personal"] = "read_only"
    desktop_login_users: list[str] = Field(default_factory=list, max_length=16)
    browser_cdp: bool = False
    browser_allowed_origins: list[str] = Field(default_factory=list, max_length=16)
    plugin_config: Path | None = None
    ca_file: Path | None = None

    @model_validator(mode="after")
    def fixed_paths(self) -> "ServiceConfig":
        if not all(
            path.is_absolute() for path in (self.credentials, self.data_dir, self.workspace)
        ):
            raise ValueError("service paths must be absolute")
        if len(set(self.desktop_login_users)) != len(self.desktop_login_users):
            raise ValueError("service login user SIDs must be distinct")
        if self.plugin_config is not None and not self.plugin_config.is_absolute():
            raise ValueError("service plugin configuration path must be absolute")
        if self.ca_file is not None and not self.ca_file.is_absolute():
            raise ValueError("service CA certificate path must be absolute")
        if self.allowed_workspaces:
            WorkspacePaths(self.workspace, tuple(self.allowed_workspaces))
        return self

    def check_identity(self, actor: Identity) -> None:
        LoginEndpoint(
            device_id="service_identity", agent_sid=self.agent_sid, service_sid=self.service_sid
        ).check_agent(actor)
        if actor.session != 0:
            raise PermissionError("SCM Agent host must run in Session 0")


def load_service_config(path: Path) -> ServiceConfig:
    from racp_agent.providers.paths import is_link

    if is_link(path.lstat()) or path.stat().st_size > 16384:
        raise PermissionError("invalid service configuration file")
    return ServiceConfig.model_validate_json(path.read_text(encoding="utf-8"))
