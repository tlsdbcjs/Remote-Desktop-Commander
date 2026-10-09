"""Demand-start Windows updater service and fixed-schema named-pipe IPC."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field
from racp_domain.models import RACPError
from racp_protocol.models import Identifier, StrictModel, new_id, timestamp
from racp_sdk.security import digest

from racp_gateway.config import GatewayConfig, load_gateway_config, resolve_gateway_paths
from racp_gateway.migrations import CURRENT_GATEWAY_SCHEMA
from racp_gateway.update_manifest import UpdateTrust, VerifiedRelease, verify_release_manifest
from racp_gateway.updater import (
    StagedRelease,
    UpdateJob,
    Updater,
    UpdateReceipt,
    download_verified_package,
)

UPDATER_SERVICE_NAME = "RACP Gateway Updater"
GATEWAY_SERVICE_NAME = "RACP Gateway"
PIPE_NAME = r"\\.\pipe\RACPGatewayUpdater"
MAX_IPC_BYTES = 64 * 1024


class UpdaterRequest(StrictModel):
    schema_version: Literal[1] = 1
    command: Literal["apply"] = "apply"
    instance_id: Identifier
    release_id: Identifier
    job_id: Identifier
    expected_revision: int = Field(ge=1)
    pre_update_backup_id: Identifier
    nonce: str = Field(min_length=20, max_length=128)
    expires_at: float

    @classmethod
    def create(
        cls,
        *,
        instance_id: str,
        release_id: str,
        job_id: str,
        expected_revision: int,
        pre_update_backup_id: str,
    ) -> UpdaterRequest:
        return cls(
            instance_id=instance_id,
            release_id=release_id,
            job_id=job_id,
            expected_revision=expected_revision,
            pre_update_backup_id=pre_update_backup_id,
            nonce=secrets.token_urlsafe(32),
            expires_at=time.time() + 120,
        )


class UpdaterRequestGuard:
    def __init__(self, allowed_callers: set[str]) -> None:
        self.allowed_callers = allowed_callers
        self.used_nonces: set[str] = set()

    def validate(self, caller_sid: str, request: UpdaterRequest) -> None:
        if caller_sid not in self.allowed_callers:
            raise RACPError("PERMISSION_DENIED", "updater caller is not authorized")
        now = time.time()
        if request.expires_at <= now or request.expires_at > now + 300:
            raise RACPError("UNAUTHENTICATED", "updater request expired or exceeds TTL")
        nonce = digest(request.nonce)
        if nonce in self.used_nonces:
            raise RACPError("CONFLICT", "updater request nonce was already used")
        self.used_nonces.add(nonce)


def write_pending_request(path: Path, request: UpdaterRequest) -> None:
    destination = path.resolve(strict=False)
    if destination.exists() and destination.is_symlink():
        raise RACPError("INVALID_ARGUMENT", "updater request path must not be a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def load_pending_request(path: Path) -> UpdaterRequest:
    source = path.resolve(strict=True)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_IPC_BYTES:
        raise RACPError("INVALID_ARGUMENT", "updater request file is invalid")
    return UpdaterRequest.model_validate_json(source.read_bytes())


Submitter = Callable[[UpdaterRequest], None]


class UpdaterIpcClient:
    def __init__(self, submitter: Submitter | None = None) -> None:
        self.submitter = submitter

    def submit(self, request: UpdaterRequest) -> None:
        if self.submitter is not None:
            self.submitter(request)
            return
        if os.name != "nt":
            raise RACPError("CAPABILITY_UNAVAILABLE", "Gateway updater IPC requires Windows")
        import win32con
        import win32file
        import win32pipe
        import win32serviceutil

        try:
            win32serviceutil.StartService(UPDATER_SERVICE_NAME)
        except Exception as exc:
            if "1056" not in str(exc):
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE",
                    "failed to start updater service",
                ) from exc
        try:
            win32pipe.WaitNamedPipe(PIPE_NAME, 10_000)
            handle = win32file.CreateFile(
                PIPE_NAME,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0,
                None,
                win32con.OPEN_EXISTING,
                0,
                None,
            )
            try:
                payload = request.model_dump_json().encode("utf-8")
                if len(payload) > MAX_IPC_BYTES:
                    raise RACPError("RESOURCE_EXHAUSTED", "updater IPC request is too large")
                win32file.WriteFile(handle, payload)
                _, response = win32file.ReadFile(handle, MAX_IPC_BYTES)
            finally:
                handle.Close()
        except RACPError:
            raise
        except Exception as exc:
            raise RACPError("CAPABILITY_UNAVAILABLE", "updater IPC handoff failed") from exc
        try:
            document = json.loads(bytes(response).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RACPError("CAPABILITY_UNAVAILABLE", "updater IPC response was invalid") from exc
        if document != {"accepted": True, "job_id": request.job_id}:
            raise RACPError("CAPABILITY_UNAVAILABLE", "updater rejected the apply request")


def _feed_origin(url: str) -> str:
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def _service_binary(service_name: str) -> str:
    import win32service

    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        service = win32service.OpenService(manager, service_name, win32service.SERVICE_QUERY_CONFIG)
        try:
            return str(win32service.QueryServiceConfig(service)[3])
        finally:
            win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(manager)


def _change_service_binary(service_name: str, binary_path: str) -> None:
    import win32service

    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        service = win32service.OpenService(
            manager,
            service_name,
            win32service.SERVICE_CHANGE_CONFIG,
        )
        try:
            win32service.ChangeServiceConfig(
                service,
                win32service.SERVICE_NO_CHANGE,
                win32service.SERVICE_NO_CHANGE,
                win32service.SERVICE_NO_CHANGE,
                binary_path,
                None,
                0,
                None,
                None,
                None,
                None,
            )
        finally:
            win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(manager)


def _bundle_root(staged: StagedRelease) -> Path:
    candidates = sorted((staged.root / "release").rglob("runtime/python.exe"))
    if len(candidates) != 1:
        raise RACPError("INVALID_ARGUMENT", "verified release must contain one Gateway runtime")
    return candidates[0].parent.parent


def _release_facts(runtime: Path) -> tuple[str, int]:
    completed = subprocess.run(
        [
            str(runtime),
            "-I",
            "-B",
            "-c",
            (
                "from racp_domain.version import VERSION; "
                "from racp_gateway.migrations import CURRENT_GATEWAY_SCHEMA; "
                "print(VERSION); print(CURRENT_GATEWAY_SCHEMA)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    lines = completed.stdout.splitlines()
    if len(lines) != 2:
        raise RACPError("INVALID_ARGUMENT", "verified release runtime metadata is invalid")
    return lines[0].strip(), int(lines[1].strip())


def install_verified_release(staged: StagedRelease, release: VerifiedRelease) -> tuple[Path, int]:
    source = _bundle_root(staged)
    runtime = source / "runtime" / "python.exe"
    version, schema = _release_facts(runtime)
    if version != release.version:
        raise RACPError("CONFLICT", "release runtime version differs from signed manifest")
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    destination = program_files / "RACP" / "Gateway" / release.version
    marker = destination / ".racp-release.json"
    if destination.exists():
        if not marker.is_file():
            raise RACPError("CONFLICT", "target release directory already exists")
        document = json.loads(marker.read_text(encoding="utf-8"))
        if document.get("release_id") != release.release_id:
            raise RACPError("CONFLICT", "target release directory belongs to another release")
        return destination, schema
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".installing")
    if temporary.exists():
        raise RACPError("CONFLICT", "incomplete target release directory already exists")
    shutil.copytree(source, temporary, symlinks=False)
    (temporary / ".racp-release.json").write_text(
        json.dumps(
            {
                "release_id": release.release_id,
                "version": release.version,
                "manifest_sha256": release.manifest_sha256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination, schema


class WindowsGatewayUpdateHost:
    def __init__(
        self,
        config: GatewayConfig,
        config_path: Path,
        release_root: Path,
        schema_version: int,
        active_release_file: Path,
        pre_update_manifest: Path,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.release_root = release_root
        self.schema_version = schema_version
        self.active_release_file = active_release_file
        self.pre_update_manifest = pre_update_manifest
        self.previous_binary = _service_binary(GATEWAY_SERVICE_NAME)
        self.previous_updater_binary = _service_binary(UPDATER_SERVICE_NAME)
        self.new_binary = f'"{release_root / "runtime" / "pythonservice.exe"}"'
        self.new_updater_binary = f'"{release_root / "runtime" / "pythonservice.exe"}"'

    def stop_gateway(self) -> None:
        import win32serviceutil

        try:
            win32serviceutil.StopService(GATEWAY_SERVICE_NAME)
        except Exception as exc:
            if "1062" not in str(exc):
                raise
        win32serviceutil.WaitForServiceStatus(GATEWAY_SERVICE_NAME, 1, 30)

    def start_gateway(self) -> None:
        active = (
            json.loads(self.active_release_file.read_text(encoding="utf-8"))
            if self.active_release_file.is_file()
            else {}
        )
        binary = self.previous_binary if active.get("rollback") else self.new_binary
        updater_binary = (
            self.previous_updater_binary if active.get("rollback") else self.new_updater_binary
        )
        _change_service_binary(GATEWAY_SERVICE_NAME, binary)
        _change_service_binary(UPDATER_SERVICE_NAME, updater_binary)
        import win32serviceutil

        win32serviceutil.StartService(GATEWAY_SERVICE_NAME)
        win32serviceutil.WaitForServiceStatus(GATEWAY_SERVICE_NAME, 4, 30)

    def health(self, release: VerifiedRelease) -> bool:
        scheme = "https" if self.config.tls.certificate_file else "http"
        url = f"{scheme}://127.0.0.1:{self.config.port}/healthz"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with httpx.Client(verify=False, trust_env=False, timeout=3) as client:
                    response = client.get(url)
                if response.status_code == 200 and response.json() == {"status": "ok"}:
                    connection = sqlite3.connect(resolve_gateway_paths(self.config).database)
                    try:
                        row = connection.execute(
                            "SELECT MAX(version) FROM gateway_schema"
                        ).fetchone()
                        schema = int(row[0] or 0)
                    finally:
                        connection.close()
                    version_ok = release.version == self.release_root.name
                    return schema == self.schema_version and version_ok
            except (OSError, ValueError, httpx.HTTPError, sqlite3.Error):
                pass
            time.sleep(0.5)
        return False

    def restore_pre_update(self, backup_id: str) -> None:
        manifest = json.loads(self.pre_update_manifest.read_text(encoding="utf-8"))
        if manifest.get("id") != backup_id:
            raise RACPError("CONFLICT", "pre-update backup identity changed")
        root = self.pre_update_manifest.parent.resolve()
        paths = resolve_gateway_paths(self.config)
        targets = {
            "gateway.db": paths.database,
            "gateway.json": paths.config_file,
        }
        for item in manifest["files"]:
            relative = str(item["path"])
            if relative.startswith("secrets/"):
                targets[relative] = paths.secrets / relative.removeprefix("secrets/")
            elif relative.startswith("artifacts/"):
                targets[relative] = paths.artifacts / relative.removeprefix("artifacts/")
        for relative, target in targets.items():
            source = (root / relative).resolve(strict=True)
            if not source.is_relative_to(root):
                raise RACPError("INVALID_ARGUMENT", "pre-update backup path escapes its root")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".update-rollback.tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)


def _lookup_backup_manifest(database: Path, backup_id: str) -> Path:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT manifest_path FROM backup_sets WHERE id=? AND state='COMPLETE'",
            (backup_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RACPError("NOT_FOUND", "pre-update backup was not found")
    return Path(str(row[0])).resolve(strict=True)


def _maintenance_state(database: Path, job_id: str) -> str:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT state FROM maintenance_jobs WHERE id=? AND kind='update'",
            (job_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RACPError("NOT_FOUND", "update maintenance job was not found")
    return str(row[0])


def _finalize_maintenance(database: Path, receipt: UpdateReceipt) -> None:
    connection = sqlite3.connect(database)
    try:
        now = timestamp()
        receipt_id = new_id("mrc")
        payload = receipt.model_dump(mode="json")
        connection.execute(
            "INSERT OR REPLACE INTO maintenance_receipts("
            "id,job_id,kind,state,payload,created_at) VALUES (?,?,?,?,?,?)",
            (receipt_id, receipt.job_id, "update", receipt.state, json.dumps(payload), now),
        )
        connection.execute(
            "UPDATE maintenance_jobs SET state=?,updated_at=?,progress=1,error=?,receipt_id=? "
            "WHERE id=? AND kind='update'",
            (receipt.state, now, receipt.error, receipt_id, receipt.job_id),
        )
        connection.execute(
            "INSERT OR REPLACE INTO update_jobs("
            "id,release_id,state,receipt_path,created_at,updated_at) "
            "VALUES (?,?,?,?,COALESCE((SELECT created_at FROM update_jobs WHERE id=?),?),?)",
            (
                receipt.job_id,
                receipt.release_id,
                receipt.state,
                None,
                receipt.job_id,
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def execute_request(config_path: Path, request: UpdaterRequest) -> UpdateReceipt | None:
    config = load_gateway_config(config_path)
    if config.instance_id != request.instance_id or config.revision != request.expected_revision:
        raise RACPError("CONFLICT", "Gateway update request no longer matches active configuration")
    paths = resolve_gateway_paths(config)
    state = _maintenance_state(paths.database, request.job_id)
    if state in {"SUCCEEDED", "FAILED"}:
        return None
    if not config.update.feed_url or not config.update.trust_key_file:
        raise RACPError("CAPABILITY_UNAVAILABLE", "signed update configuration is incomplete")
    candidate = paths.state_root / "updates" / "candidates" / f"{request.release_id}.json"
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 64 * 1024:
        raise RACPError("NOT_FOUND", "verified update candidate was not found")
    release = verify_release_manifest(
        candidate.read_bytes(),
        UpdateTrust(
            public_key_file=Path(config.update.trust_key_file),
            allowed_origins=[_feed_origin(config.update.feed_url)],
            current_version=__import__("racp_domain.version", fromlist=["VERSION"]).VERSION,
            updater_version=__import__("racp_domain.version", fromlist=["VERSION"]).VERSION,
            gateway_schema=CURRENT_GATEWAY_SCHEMA,
            platform="win-x64",
        ),
    )
    if release.release_id != request.release_id:
        raise RACPError("CONFLICT", "verified update candidate identity changed")
    pre_update_manifest = _lookup_backup_manifest(paths.database, request.pre_update_backup_id)
    updater = Updater(paths.update_staging, download_verified_package)
    staged = updater.stage(release)
    release_root, schema_version = install_verified_release(staged, release)
    active = paths.state_root / "updates" / "active-release.json"
    receipt_file = paths.state_root / "updates" / "receipts" / f"{request.job_id}.json"
    host = WindowsGatewayUpdateHost(
        config,
        config_path,
        release_root,
        schema_version,
        active,
        pre_update_manifest,
    )
    receipt = updater.apply(
        UpdateJob(
            id=request.job_id,
            current_version=__import__("racp_domain.version", fromlist=["VERSION"]).VERSION,
            active_release_file=active,
            receipt_file=receipt_file,
            pre_update_backup_id=request.pre_update_backup_id,
        ),
        release,
        staged,
        host,
    )
    _finalize_maintenance(paths.database, receipt)
    if receipt.state in {"SUCCEEDED", "FAILED"}:
        (paths.state_root / "updates" / "pending.json").unlink(missing_ok=True)
    return receipt


def _gateway_service_sid() -> str:
    import win32security

    sid, _, _ = win32security.LookupAccountName(None, r"NT SERVICE\RACP Gateway")
    return str(win32security.ConvertSidToStringSid(sid))


def _caller_sid(pipe: Any) -> str:
    import win32api
    import win32con
    import win32pipe
    import win32security

    win32pipe.ImpersonateNamedPipeClient(pipe)
    try:
        token = win32security.OpenThreadToken(
            win32api.GetCurrentThread(),
            win32con.TOKEN_QUERY,
            True,
        )
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return str(win32security.ConvertSidToStringSid(sid))
    finally:
        win32security.RevertToSelf()


def _create_pipe() -> Any:
    import pywintypes
    import win32pipe
    import win32security

    gateway_sid = _gateway_service_sid()
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;{gateway_sid})",
        win32security.SDDL_REVISION_1,
    )
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    return win32pipe.CreateNamedPipe(
        PIPE_NAME,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
        1,
        MAX_IPC_BYTES,
        MAX_IPC_BYTES,
        0,
        attributes,
    )


def receive_request(pending_path: Path) -> UpdaterRequest:
    import win32file
    import win32pipe

    pipe = _create_pipe()
    try:
        win32pipe.ConnectNamedPipe(pipe, None)
        _, raw = win32file.ReadFile(pipe, MAX_IPC_BYTES)
        request = UpdaterRequest.model_validate_json(bytes(raw))
        UpdaterRequestGuard({_gateway_service_sid()}).validate(_caller_sid(pipe), request)
        pending = load_pending_request(pending_path)
        if pending != request:
            raise RACPError("CONFLICT", "updater IPC request differs from the pending request")
        win32file.WriteFile(
            pipe,
            json.dumps({"accepted": True, "job_id": request.job_id}).encode("utf-8"),
        )
        win32file.FlushFileBuffers(pipe)
        return request
    finally:
        try:
            win32pipe.DisconnectNamedPipe(pipe)
        except Exception:
            pass
        win32file.CloseHandle(pipe)


def run_updater_service(config_path: Path) -> int:
    config = load_gateway_config(config_path)
    pending_path = resolve_gateway_paths(config).state_root / "updates" / "pending.json"
    if not pending_path.is_file():
        return 0
    pending = load_pending_request(pending_path)
    receipt = (
        resolve_gateway_paths(config).state_root
        / "updates"
        / "receipts"
        / f"{pending.job_id}.json"
    )
    request = pending if receipt.is_file() else receive_request(pending_path)
    execute_request(config_path, request)
    return 0


if os.name == "nt":
    import win32service as _win32service
    import win32serviceutil as _win32serviceutil

    class GatewayUpdaterService(_win32serviceutil.ServiceFramework):  # type: ignore[misc]
        """Static pywin32 entry point loaded by pythonservice.exe."""

        _svc_name_ = UPDATER_SERVICE_NAME
        _svc_display_name_ = UPDATER_SERVICE_NAME

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            config_file = _win32serviceutil.GetServiceCustomOption(args[0], "ConfigFile")
            if not config_file:
                raise ValueError("Gateway updater ConfigFile option is missing")
            self.config_path = Path(str(config_file))
            self.stop_requested = False

        def SvcStop(self) -> None:
            self.stop_requested = True
            self.ReportServiceStatus(_win32service.SERVICE_STOP_PENDING)

        def SvcRun(self) -> None:
            self.ReportServiceStatus(_win32service.SERVICE_START_PENDING, waitHint=15_000)
            self.ReportServiceStatus(_win32service.SERVICE_RUNNING)
            if not self.stop_requested:
                run_updater_service(self.config_path)


def service_class(config_path: Path) -> Any:
    import win32service
    import win32serviceutil

    class GatewayUpdaterService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = UPDATER_SERVICE_NAME
        _svc_display_name_ = UPDATER_SERVICE_NAME

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            self.stop_requested = False

        def SvcStop(self) -> None:
            self.stop_requested = True
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)

        def SvcDoRun(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            if not self.stop_requested:
                run_updater_service(config_path)

    return GatewayUpdaterService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise OSError("Gateway updater service requires Windows")
    import servicemanager

    configured = service_class(args.config_file)
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(configured)
    servicemanager.StartServiceCtrlDispatcher()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
