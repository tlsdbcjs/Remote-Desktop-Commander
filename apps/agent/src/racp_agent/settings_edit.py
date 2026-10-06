"""Local settings repair preserving the protected Device identity and journal."""

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from racp_domain.models import RACPError
from racp_sdk.security import SecretStore, digest

from racp_agent.connect import credential_document
from racp_agent.instance_lock import InstanceLock, InstanceRunningError
from racp_agent.settings import AgentSettings, local_path, stored_settings
from racp_agent.workspaces import WorkspaceSpec


def settings_lock(credentials: Path) -> Path:
    return credentials.parent / (credentials.name + ".settings.lock")


def snapshot(credentials: Path) -> tuple[dict[str, str], str]:
    local_path(credentials)
    value = SecretStore(credentials).load()
    if any(key not in value for key in ("gateway", "device_id", "credential", "agent_settings")):
        raise ValueError("Protected registration is incomplete")
    if not 20 <= len(value["credential"]) <= 128:
        raise ValueError("Protected Device credential is invalid")
    with credentials.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Protected settings exceed their bound")
    return value, hashlib.sha256(raw).hexdigest()


def editable_settings(credentials: Path) -> dict[str, Any]:
    with InstanceLock(settings_lock(credentials)):
        value, revision = snapshot(credentials)
        settings = stored_settings(value)
        if settings is None:
            raise ValueError("Versioned settings are required")
        return {
            "gateway": settings.gateway,
            "device_id": settings.device_id,
            "workspace": str(settings.workspace),
            "allowed_workspaces": [
                item.model_dump(mode="json") for item in settings.allowed_workspaces
            ],
            "profile": settings.profile,
            "ca_file": str(settings.ca_file) if settings.ca_file else None,
            "revision": revision,
            "desktop_enabled": settings.desktop_enabled,
        }


def update_settings(
    credentials: Path,
    revision: str,
    gateway: str,
    workspace: Path,
    profile: str,
    ca_file: Path | None,
    allowed_workspaces: list[WorkspaceSpec],
    desktop_enabled: bool | None = None,
) -> None:
    try:
        with InstanceLock(settings_lock(credentials)):
            value, current = snapshot(credentials)
            if current != revision:
                raise RACPError("SETTINGS_CHANGED", "Reload the settings before saving")
            previous = stored_settings(value)
            if previous is None:
                raise ValueError("Versioned settings are required")
            # A foreground, background or service Agent holds this same lifetime lock.
            with InstanceLock(
                credentials.parent / ("agent-" + digest(previous.device_id) + ".lock")
            ):
                pending = previous.model_dump()
                pending.update(
                    gateway=gateway,
                    workspace=workspace,
                    profile=profile,
                    ca_file=ca_file,
                    allowed_workspaces=allowed_workspaces,
                )
                if desktop_enabled is not None:
                    pending["desktop_enabled"] = desktop_enabled
                settings = AgentSettings.model_validate(pending)
                replacement = credential_document(settings, value["credential"])
                if len(settings.model_dump_json().encode("utf-8")) > 16384:
                    raise ValueError("Settings exceed their bound")
                # Back up with the same OS protection before atomically replacing the original.
                backup = local_path(
                    credentials.parent / "settings-backups" / (uuid4().hex + ".bin")
                )
                SecretStore(backup).save(value, overwrite=False)
                SecretStore(credentials).save(replacement)
    except InstanceRunningError:
        raise RACPError("SETTINGS_BUSY", "Stop the Agent before editing settings") from None
