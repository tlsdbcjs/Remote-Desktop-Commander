"""Bounded stdin/stdout bridge for the desktop client; no arbitrary execution API."""

import argparse
import asyncio
import json
import os
import ssl
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field, TypeAdapter, ValidationError
from racp_domain.models import RACPError
from racp_policy.permissions import LocalPermissions
from racp_protocol.models import StrictModel
from racp_sdk.security import SecretStore, tls_context

from racp_agent.background import launch_arguments, start, status, stop
from racp_agent.client_activity import activity
from racp_agent.connect import enroll
from racp_agent.connection_import import imported_ca, load_connection
from racp_agent.execution_identity import execution_identity
from racp_agent.main import parser as agent_parser
from racp_agent.settings import gateway_origin, local_path, saved_settings
from racp_agent.settings_edit import editable_settings, update_settings
from racp_agent.workspaces import WorkspaceSpec


class Command(StrictModel):
    action: Literal["info", "start", "status", "stop", "activity", "settings"]


class Enrollment(StrictModel):
    action: Literal["enroll"]
    gateway: str = Field(max_length=2048)
    workspace: Path
    ca_file: Path | None = None
    profile: Literal["read_only", "standard", "trusted_personal"] = "read_only"
    token: str = Field(min_length=20, max_length=128, repr=False)
    allowed_workspaces: list[WorkspaceSpec] = Field(default_factory=list, max_length=15)
    desktop_enabled: bool = False
    permissions: LocalPermissions | None = None


class InspectConnection(StrictModel):
    action: Literal["inspect_connection"]
    path: Path


class ImportConnection(StrictModel):
    action: Literal["enroll_connection"]
    path: Path
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace: Path
    profile: Literal["read_only", "standard", "trusted_personal"] = "read_only"
    allowed_workspaces: list[WorkspaceSpec] = Field(default_factory=list, max_length=15)
    desktop_enabled: bool = False
    permissions: LocalPermissions | None = None


class SettingsUpdate(StrictModel):
    action: Literal["update_settings"]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    gateway: str = Field(max_length=2048)
    workspace: Path
    ca_file: Path | None = None
    profile: Literal["read_only", "standard", "trusted_personal"]
    allowed_workspaces: list[WorkspaceSpec] = Field(default_factory=list, max_length=15)
    desktop_enabled: bool | None = None
    permissions: LocalPermissions | None = None


DesktopRequest = Command | Enrollment | InspectConnection | ImportConnection | SettingsUpdate
REQUEST: TypeAdapter[DesktopRequest] = TypeAdapter(
    Annotated[DesktopRequest, Field(discriminator="action")]
)


def failure_code(error: Exception, request: DesktopRequest | None) -> str:
    """Return a closed diagnostic vocabulary, never exception text or request values."""
    if isinstance(error, RACPError):
        return {
            "UNAUTHENTICATED": "TOKEN_REJECTED",
            "CONFLICT": "REGISTRATION_CONFLICT",
            "EXECUTION_UNKNOWN": "REGISTRATION_STORAGE_FAILED",
            "INVALID_ARGUMENT": "REQUEST_INVALID",
            "TRANSPORT_ERROR": "GATEWAY_REJECTED",
            "GATEWAY_INVALID": "GATEWAY_INVALID",
            "CA_INVALID": "CA_INVALID",
            "WORKSPACE_INVALID": "WORKSPACE_INVALID",
            "CONNECTION_FILE_INVALID": "CONNECTION_FILE_INVALID",
            "CONNECTION_FILE_EXPIRED": "CONNECTION_FILE_EXPIRED",
            "CONNECTION_FILE_CHANGED": "CONNECTION_FILE_CHANGED",
            "SETTINGS_BUSY": "SETTINGS_BUSY",
            "SETTINGS_CHANGED": "SETTINGS_CHANGED",
        }.get(error.error.code, "REQUEST_FAILED")
    if request is not None and request.action in {"info", "settings"}:
        return "CONFIG_UNREADABLE"
    if isinstance(error, ValidationError):
        if any("permissions" in item["loc"] for item in error.errors(include_input=False)):
            return "PERMISSIONS_INVALID"
        if any("token" in item["loc"] for item in error.errors(include_input=False)):
            return "TOKEN_INVALID"
        return "REQUEST_INVALID"
    cause: BaseException | None = error
    for _ in range(12):
        if isinstance(cause, ssl.SSLError):
            return "TLS_FAILED"
        if cause is None:
            break
        cause = cause.__cause__ or cause.__context__
    if isinstance(error, httpx.TimeoutException):
        return "GATEWAY_TIMEOUT"
    if isinstance(error, httpx.HTTPError):
        return "GATEWAY_UNREACHABLE"
    if isinstance(error, (OSError, ValueError)):
        return "LOCAL_STATE_FAILED"
    return "REQUEST_FAILED"


def enrollment_preflight(request: Enrollment) -> None:
    try:
        gateway_origin(request.gateway)
    except ValueError:
        raise RACPError("GATEWAY_INVALID", "Invalid Gateway origin") from None
    try:
        for folder in [request.workspace, *(item.path for item in request.allowed_workspaces)]:
            if not local_path(folder).is_dir():
                raise ValueError("Workspace must be an existing local directory")
    except (OSError, ValueError):
        raise RACPError("WORKSPACE_INVALID", "Invalid local workspace") from None
    if request.ca_file is not None:
        try:
            tls_context(local_path(request.ca_file))
        except (OSError, ValueError):
            raise RACPError("CA_INVALID", "Invalid local CA certificate") from None


def information(credentials: Path) -> dict[str, Any]:
    value: dict[str, Any] = {
        "configured": False,
        "execution_identity": execution_identity(),
        "desktop_supported": os.name == "nt",
    }
    if credentials.exists():
        settings = saved_settings(SecretStore(credentials).load())
        if settings is None:
            raise ValueError("Desktop client requires versioned enrollment settings")
        value.update(
            configured=True,
            gateway=settings.gateway,
            device_id=settings.device_id,
            workspace=str(settings.workspace),
            profile=settings.profile,
            allowed_workspaces=[
                item.model_dump(mode="json") for item in settings.allowed_workspaces
            ],
            ca_file=str(settings.ca_file) if settings.ca_file else None,
            desktop_enabled=settings.desktop_enabled,
            permissions=settings.permissions.model_dump(mode="json"),
        )
    return value


async def execute(request: DesktopRequest, state_dir: Path) -> dict[str, Any]:
    root = state_dir
    credentials = local_path(root / "credential.bin")
    if isinstance(request, SettingsUpdate):
        pending = Enrollment(
            action="enroll",
            gateway=request.gateway,
            workspace=request.workspace,
            ca_file=request.ca_file,
            profile=request.profile,
            token="unused-settings-preflight",
            allowed_workspaces=request.allowed_workspaces,
        )
        await asyncio.to_thread(enrollment_preflight, pending)
        await asyncio.to_thread(
            update_settings,
            credentials,
            request.revision,
            request.gateway,
            request.workspace,
            request.profile,
            request.ca_file,
            request.allowed_workspaces,
            request.desktop_enabled,
            request.permissions,
        )
        return information(credentials)
    if isinstance(request, InspectConnection):
        value, fingerprint = await asyncio.to_thread(load_connection, request.path)
        return value.preview(fingerprint)
    if isinstance(request, ImportConnection):
        value, _ = await asyncio.to_thread(load_connection, request.path, request.file_sha256)
        if credentials.exists():
            raise RACPError("CONFLICT", "Device is already registered")
        pending = Enrollment(
            action="enroll",
            gateway=value.gateway,
            workspace=request.workspace,
            token=value.token,
            profile=request.profile,
            allowed_workspaces=request.allowed_workspaces,
            desktop_enabled=request.desktop_enabled,
            permissions=request.permissions,
        )
        await asyncio.to_thread(enrollment_preflight, pending)
        pending.ca_file = await asyncio.to_thread(imported_ca, root, value)
        return await execute(pending, state_dir)
    if isinstance(request, Enrollment):
        await asyncio.to_thread(enrollment_preflight, request)
        await asyncio.to_thread(
            enroll,
            request.gateway,
            request.workspace,
            root,
            request.token,
            profile=request.profile,
            ca_file=request.ca_file,
            allowed_workspaces=tuple(request.allowed_workspaces),
            desktop_enabled=request.desktop_enabled,
            permissions=request.permissions,
        )
        return information(credentials)
    if request.action == "info":
        return information(credentials)
    if request.action == "settings":
        return await asyncio.to_thread(editable_settings, credentials)
    if request.action == "activity":
        return await asyncio.to_thread(activity, credentials)
    if request.action == "status":
        return await status(credentials)
    if request.action == "stop":
        return await stop(credentials)
    options = agent_parser().parse_args(["--credentials", str(credentials)])
    return await start(credentials, options, launch_arguments(options))


def main() -> None:
    parser = argparse.ArgumentParser(description="Private RACP desktop client bridge")
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args()
    request: DesktopRequest | None = None
    try:
        raw = sys.stdin.buffer.readline(16386)
        if len(raw) > 16384:
            raise ValueError("request exceeds bound")
        request = REQUEST.validate_json(raw)
        result = asyncio.run(execute(request, local_path(args.state_dir.absolute())))
        response = {"ok": True, "result": result}
    except (
        OSError,
        ValueError,
        RuntimeError,
        RACPError,
        httpx.HTTPError,
        ValidationError,
    ) as error:
        response = {
            "ok": False,
            "code": failure_code(error, request),
        }
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    if response["ok"] is not True:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
