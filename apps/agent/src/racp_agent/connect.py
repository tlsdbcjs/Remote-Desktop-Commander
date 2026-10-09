"""One-command PC enrollment followed by the normal outbound foreground Agent."""

import argparse
import getpass
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field
from racp_domain.models import RACPError
from racp_policy.permissions import LocalPermissions, legacy_permissions
from racp_protocol.models import Identifier, StrictModel
from racp_sdk.security import SecretStore, tls_context

from racp_agent.execution_identity import execution_identity
from racp_agent.settings import AgentSettings, default_state_dir, local_path
from racp_agent.workspaces import WorkspaceSpec, workspace_argument


class EnrollmentResult(StrictModel):
    device_id: Identifier
    credential: str = Field(min_length=20, max_length=128, repr=False)


def credential_document(settings: AgentSettings, credential: str) -> dict[str, str]:
    value = {
        "gateway": settings.gateway,
        "device_id": settings.device_id,
        "credential": credential,
        "agent_settings": settings.model_dump_json(),
    }
    if settings.ca_file is not None:
        value["ca_file"] = str(settings.ca_file)
    return value


def prepare(
    gateway: str,
    workspace: Path,
    state_dir: Path,
    *,
    profile: Literal["read_only", "standard", "trusted_personal"] = "read_only",
    ca_file: Path | None = None,
    allowed_workspaces: tuple[WorkspaceSpec, ...] = (),
    desktop_enabled: bool = False,
    permissions: LocalPermissions | None = None,
) -> tuple[Path, AgentSettings]:
    root = local_path(Path(os.path.abspath(state_dir)))
    credential_store = root / "credential.bin"
    local_path(credential_store)
    settings = AgentSettings(
        version=2,
        permissions=permissions
        if permissions is not None
        else legacy_permissions(desktop_enabled=desktop_enabled),
        gateway=gateway,
        device_id="dev_pending",
        workspace=Path(os.path.abspath(workspace)),
        data_dir=root / "data",
        profile=profile,
        ca_file=Path(os.path.abspath(ca_file)) if ca_file else None,
        allowed_workspaces=list(allowed_workspaces),
        desktop_enabled=desktop_enabled,
    )
    if credential_store.exists():
        raise RACPError("CONFLICT", "Device is already configured; start racp-agent instead")
    maximum = settings.model_copy(update={"device_id": "d" * 96})
    if len(maximum.model_dump_json().encode("utf-8")) > 16384:
        raise ValueError("Agent settings exceed the credential storage allowance")
    if (
        len(json.dumps(credential_document(maximum, "x" * 128), ensure_ascii=False).encode())
        > 32768
    ):
        raise ValueError("Agent settings exceed the protected file allowance")
    if os.path.lexists(root / ".enrollment-in-progress"):
        raise RACPError("CONFLICT", "Another enrollment or interrupted setup requires inspection")
    return credential_store, settings


def enroll(
    gateway: str,
    workspace: Path,
    state_dir: Path,
    secret: str,
    *,
    profile: Literal["read_only", "standard", "trusted_personal"] = "read_only",
    ca_file: Path | None = None,
    allowed_workspaces: tuple[WorkspaceSpec, ...] = (),
    desktop_enabled: bool = False,
    permissions: LocalPermissions | None = None,
) -> tuple[Path, AgentSettings]:
    credential_store, settings = prepare(
        gateway,
        workspace,
        state_dir,
        profile=profile,
        ca_file=ca_file,
        allowed_workspaces=allowed_workspaces,
        desktop_enabled=desktop_enabled,
        permissions=permissions,
    )
    root = credential_store.parent
    if not 20 <= len(secret) <= 128 or any(char.isspace() for char in secret):
        raise RACPError("INVALID_ARGUMENT", "invalid enrollment token")
    # Validate local permissions before consuming a one-use server token.
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    reservation = root / ".enrollment-in-progress"
    try:
        descriptor = os.open(reservation, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RACPError(
            "CONFLICT", "Another enrollment or interrupted setup requires inspection"
        ) from exc
    try:
        os.close(descriptor)
        with httpx.Client(
            verify=tls_context(settings.ca_file) or True,
            follow_redirects=False,
            trust_env=False,
            timeout=15,
        ) as http:
            with http.stream(
                "POST", settings.gateway + "/agent/v1/enroll", json={"token": secret}
            ) as response:
                if response.status_code != 200:
                    raise RACPError(
                        "UNAUTHENTICATED"
                        if response.status_code in {401, 403}
                        else "TRANSPORT_ERROR",
                        "Enrollment rejected; check Gateway and one-use token",
                    )
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4096:
                        raise ValueError("Enrollment response exceeds 4 KiB")
        result = EnrollmentResult.model_validate_json(raw)
        settings = settings.model_copy(update={"device_id": result.device_id})
        value = credential_document(settings, result.credential)
        try:
            SecretStore(credential_store).save(value, overwrite=False)
        except OSError as exc:
            raise RACPError(
                "EXECUTION_UNKNOWN",
                "Device registered but local credential storage failed; use a new enrollment",
                execution_state="unknown",
                device_id=result.device_id,
            ) from exc
        return credential_store, settings
    finally:
        reservation.unlink(missing_ok=True)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Register this PC and connect its RACP Agent")
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--allow-workspace",
        action="append",
        type=workspace_argument,
        default=[],
        help="Additional approved folder, ID=PATH",
    )
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument(
        "--profile", choices=["read_only", "standard", "trusted_personal"], default="read_only"
    )
    parser.add_argument("--token-stdin", action="store_true")
    parser.add_argument("--configure-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        prepare(
            args.gateway,
            args.workspace,
            args.state_dir,
            profile=args.profile,
            ca_file=args.ca_file,
            allowed_workspaces=tuple(args.allow_workspace),
        )
        secret = (
            sys.stdin.readline(130).strip()
            if args.token_stdin
            else getpass.getpass("One-use enrollment token: ")
        )
        credential_store, settings = enroll(
            args.gateway,
            args.workspace,
            args.state_dir,
            secret,
            profile=args.profile,
            ca_file=args.ca_file,
            allowed_workspaces=tuple(args.allow_workspace),
        )
        print(
            json.dumps(
                {
                    "state": "configured",
                    "device_id": settings.device_id,
                    "gateway": settings.gateway,
                    "workspace": str(settings.workspace),
                    "allowed_workspaces": [
                        item.model_dump(mode="json") for item in settings.allowed_workspaces
                    ],
                    "profile": settings.profile,
                    "execution_identity": execution_identity(),
                    "credential_store": str(credential_store),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if not args.configure_only:
            from racp_agent.main import run

            run(credential_store)
    except RACPError as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": exc.error.code,
                        "message": exc.error.message,
                        "details": exc.error.details,
                    }
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(3 if exc.error.code == "UNAUTHENTICATED" else 4) from None
    except (OSError, ValueError, httpx.HTTPError):
        print(
            "PC setup failed; check local paths, certificate and Gateway connection",
            file=sys.stderr,
        )
        raise SystemExit(4) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
