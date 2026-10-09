"""FastAPI routes for the authenticated Gateway management plane."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import FileResponse
from racp_domain.models import RACPError
from racp_protocol.management import (
    BackupCreateRequest,
    BackupSetView,
    ConfigCheck,
    DeviceGroupCreateRequest,
    DeviceGroupPatchRequest,
    DeviceGroupsPatch,
    DeviceGroupView,
    DeviceMetadataView,
    DevicePage,
    DeviceQuery,
    GatewayStatusView,
    LogExportCreateRequest,
    LogExportReceiptView,
    LogPage,
    LogQuery,
    MaintenanceJobView,
    ManagementPrincipal,
    ManagementUserCreateRequest,
    ManagementUserPatchRequest,
    ManagementUserView,
    RestoreCreateRequest,
    RestorePreview,
    SettingsPatch,
    SettingsView,
    SupportBundleCreateRequest,
    SupportBundleReceiptView,
    UpdateApplyRequest,
    UpdateCandidateView,
    UpdateCheckRequest,
    UpdateStatusView,
)

from racp_gateway.backup import BackupManager
from racp_gateway.config import load_gateway_config
from racp_gateway.console_auth import COOKIE, ConsoleAuth
from racp_gateway.events import EventFeed
from racp_gateway.maintenance import MaintenanceCoordinator
from racp_gateway.management.auth import ManagementAuth
from racp_gateway.management.authorization import authorize_management
from racp_gateway.management.backups import BackupManagement
from racp_gateway.management.devices import DeviceManagement
from racp_gateway.management.log_exports import LogExportManager
from racp_gateway.management.logs import LogStore
from racp_gateway.management.settings import SettingsService
from racp_gateway.management.status import GatewayStatusService
from racp_gateway.management.store import ManagementStore
from racp_gateway.management.updates import UpdateManagement
from racp_gateway.management.users import UserManagement
from racp_gateway.restore import RestoreManager
from racp_gateway.service import ControlPlane
from racp_gateway.store import GatewayStore
from racp_gateway.support_bundle import SupportBundleRequest, create_support_bundle


def create_management_router(
    store: GatewayStore,
    console_auth: ConsoleAuth,
    events: EventFeed,
    control: ControlPlane,
    *,
    data_dir: Path,
    config_path: Path | None,
    oauth_configured: bool,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/management", tags=["management"])
    users = UserManagement(store)
    auth = ManagementAuth(console_auth, users)
    management = ManagementStore(store)
    devices = DeviceManagement(store)
    logs = LogStore(store)
    database_path = Path(str(store.db.execute("PRAGMA database_list").fetchone()[2]))
    log_exports = LogExportManager(database_path, data_dir / "log-exports")
    status = GatewayStatusService(
        store,
        management,
        config_path=config_path,
        oauth_configured=oauth_configured,
        event_stream_count=lambda: sum(events.active.values()),
    )
    settings = SettingsService(config_path, management) if config_path is not None else None
    instance_id = "gateway_legacy"
    if config_path is not None:
        try:
            instance_id = load_gateway_config(config_path).instance_id
        except (OSError, ValueError):
            pass
    maintenance = MaintenanceCoordinator(store, control)
    backups = BackupManager(
        store,
        data_dir / "backups",
        instance_id=instance_id,
        config_path=config_path,
        artifacts_root=data_dir / "artifacts",
        secrets_root=data_dir / "secrets",
    )
    backup_management = BackupManagement(
        maintenance,
        backups,
        RestoreManager(store),
        data_dir / "restores",
    )
    updates = UpdateManagement(
        config_path,
        data_dir=data_dir,
        maintenance=maintenance,
        backups=backups,
    )
    support_dir = data_dir / "support"

    async def principal(request: Request) -> ManagementPrincipal:
        credential = request.cookies.get(COOKIE, "")
        if not credential:
            raise RACPError("UNAUTHENTICATED", "management session required")
        changing = request.method not in {"GET", "HEAD", "OPTIONS"}
        if changing and request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise RACPError("PERMISSION_DENIED", "management mutation requires same-origin request")
        return auth.resolve_management_principal(
            credential,
            csrf=request.headers.get("x-csrf-token", "") if changing else None,
            touch=changing,
        )
    principal_dependency = Depends(principal)

    @router.get("/status", response_model=GatewayStatusView)
    async def management_status(
        response: Response,
        actor: ManagementPrincipal = principal_dependency,
    ) -> GatewayStatusView:
        snapshot = status.snapshot(actor)
        response.headers["X-RACP-Revision"] = str(snapshot.settings_revision)
        return snapshot

    @router.get("/settings", response_model=SettingsView)
    async def management_settings(
        response: Response,
        actor: ManagementPrincipal = principal_dependency,
    ) -> SettingsView:
        if settings is None:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "versioned Gateway configuration is not active",
            )
        view = settings.view(actor)
        response.headers["ETag"] = f'"{view.revision}"'
        response.headers["X-RACP-Revision"] = str(view.revision)
        return view

    @router.post("/settings/stage", response_model=ConfigCheck)
    async def management_settings_stage(
        input: SettingsPatch,
        request: Request,
        response: Response,
        actor: ManagementPrincipal = principal_dependency,
    ) -> ConfigCheck:
        if settings is None:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "versioned Gateway configuration is not active",
            )
        if_match = request.headers.get("if-match")
        expected_etag = f'"{input.expected_revision}"'
        if if_match is not None and if_match != expected_etag:
            raise RACPError("CONFLICT", "Gateway configuration ETag changed")
        result = settings.stage(actor, input)
        response.headers["ETag"] = expected_etag
        response.headers["X-RACP-Revision"] = str(input.expected_revision)
        return result

    @router.put("/settings", response_model=SettingsView)
    async def management_settings_apply(
        input: SettingsPatch,
        request: Request,
        response: Response,
        actor: ManagementPrincipal = principal_dependency,
    ) -> SettingsView:
        if settings is None:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "versioned Gateway configuration is not active",
            )
        if_match = request.headers.get("if-match")
        expected_etag = f'"{input.expected_revision}"'
        if if_match is not None and if_match != expected_etag:
            raise RACPError("CONFLICT", "Gateway configuration ETag changed")
        result = settings.apply(actor, input)
        response.headers["ETag"] = f'"{result.revision}"'
        response.headers["X-RACP-Revision"] = str(result.revision)
        return result

    @router.get("/devices", response_model=DevicePage)
    async def management_devices(
        search: str | None = None,
        status_filter: str | None = None,
        group_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        actor: ManagementPrincipal = principal_dependency,
    ) -> DevicePage:
        return devices.list(
            actor,
            DeviceQuery(
                search=search,
                status=status_filter,
                group_id=group_id,
                limit=limit,
                cursor=cursor,
            ),
        )

    @router.get("/users", response_model=list[ManagementUserView])
    async def management_users(
        actor: ManagementPrincipal = principal_dependency,
    ) -> list[ManagementUserView]:
        return users.list(actor)

    @router.post("/users", response_model=ManagementUserView)
    async def management_user_create(
        input: ManagementUserCreateRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> ManagementUserView:
        if actor.role != "owner":
            raise RACPError("PERMISSION_DENIED", "only the owner can create management users")
        return users.create(actor, **input.model_dump())

    @router.patch("/users/{user_id}", response_model=ManagementUserView)
    async def management_user_patch(
        user_id: str,
        input: ManagementUserPatchRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> ManagementUserView:
        if actor.role != "owner":
            raise RACPError("PERMISSION_DENIED", "only the owner can change management users")
        return users.patch(actor, user_id, **input.model_dump())

    @router.get("/groups", response_model=list[DeviceGroupView])
    async def management_groups(
        actor: ManagementPrincipal = principal_dependency,
    ) -> list[DeviceGroupView]:
        return devices.groups(actor)

    @router.post("/groups", response_model=DeviceGroupView)
    async def management_group_create(
        input: DeviceGroupCreateRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> DeviceGroupView:
        group_id = devices.create_group(actor, input.name, input.tags)
        return devices.group(actor, group_id)

    @router.patch("/groups/{group_id}", response_model=DeviceGroupView)
    async def management_group_patch(
        group_id: str,
        input: DeviceGroupPatchRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> DeviceGroupView:
        return devices.update_group(actor, group_id, **input.model_dump())

    @router.patch("/devices/{device_id}/groups", response_model=DeviceMetadataView)
    async def management_device_groups(
        device_id: str,
        input: DeviceGroupsPatch,
        actor: ManagementPrincipal = principal_dependency,
    ) -> DeviceMetadataView:
        return devices.set_groups(
            actor,
            device_id,
            input.group_ids,
            input.expected_revision,
        )

    @router.get("/logs", response_model=LogPage)
    async def management_logs(
        source: Literal["audit", "diagnostic"] | None = None,
        device_id: str | None = None,
        request_id: str | None = None,
        actor_id: str | None = None,
        from_utc: str | None = None,
        to_utc: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        actor: ManagementPrincipal = principal_dependency,
    ) -> LogPage:
        return logs.search(
            actor,
            LogQuery(
                source=source,
                device_id=device_id,
                request_id=request_id,
                actor_id=actor_id,
                from_utc=from_utc,
                to_utc=to_utc,
                limit=limit,
                cursor=cursor,
            ),
        )

    @router.post("/log-exports", response_model=LogExportReceiptView)
    async def management_log_export_create(
        input: LogExportCreateRequest,
        background: BackgroundTasks,
        actor: ManagementPrincipal = principal_dependency,
    ) -> LogExportReceiptView:
        return log_exports.create(actor, input, background)

    @router.get("/log-exports/{export_id}", response_model=LogExportReceiptView)
    async def management_log_export_status(
        export_id: str,
        actor: ManagementPrincipal = principal_dependency,
    ) -> LogExportReceiptView:
        return log_exports.status(actor, export_id)

    @router.get("/log-exports/{export_id}/download")
    async def management_log_export_download(
        export_id: str,
        actor: ManagementPrincipal = principal_dependency,
    ) -> FileResponse:
        receipt, path = log_exports.download(actor, export_id)
        media_type = "application/x-ndjson" if receipt.format == "jsonl" else "application/json"
        return FileResponse(
            path,
            media_type=media_type,
            filename=receipt.file_name,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/backups", response_model=list[BackupSetView])
    async def management_backups(
        actor: ManagementPrincipal = principal_dependency,
    ) -> list[BackupSetView]:
        authorize_management(actor, "backups.read")
        return [
            BackupSetView(**dict(row))
            for row in store.db.execute(
                "SELECT * FROM backup_sets WHERE state='COMPLETE' "
                "ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        ]

    @router.post("/backups", response_model=BackupSetView)
    async def management_backup_create(
        input: BackupCreateRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> BackupSetView:
        return await backup_management.create(actor, input.idempotency_key)

    @router.get("/backups/{backup_id}/restore-preview", response_model=RestorePreview)
    async def management_restore_preview(
        backup_id: str,
        actor: ManagementPrincipal = principal_dependency,
    ) -> RestorePreview:
        return backup_management.validate_restore(actor, backup_id)

    @router.post("/restores", response_model=MaintenanceJobView)
    async def management_restore_create(
        input: RestoreCreateRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> MaintenanceJobView:
        return await backup_management.request_restore(actor, input)

    @router.get("/maintenance-jobs/{job_id}", response_model=MaintenanceJobView)
    async def management_maintenance_job(
        job_id: str,
        actor: ManagementPrincipal = principal_dependency,
    ) -> MaintenanceJobView:
        return maintenance.get(actor, job_id)

    @router.get("/updates/status", response_model=UpdateStatusView)
    async def management_update_status(
        actor: ManagementPrincipal = principal_dependency,
    ) -> UpdateStatusView:
        return updates.status(actor)

    @router.post("/updates/check", response_model=UpdateCandidateView)
    async def management_update_check(
        input: UpdateCheckRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> UpdateCandidateView:
        return updates.check(actor, input)

    @router.post("/updates/apply", response_model=MaintenanceJobView)
    async def management_update_apply(
        input: UpdateApplyRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> MaintenanceJobView:
        return await updates.apply(actor, input)

    @router.post("/support-bundles", response_model=SupportBundleReceiptView)
    async def management_support_bundle_create(
        input: SupportBundleCreateRequest,
        actor: ManagementPrincipal = principal_dependency,
    ) -> SupportBundleReceiptView:
        authorize_management(actor, "logs.export")
        receipt = create_support_bundle(
            SupportBundleRequest(
                store=store,
                output_dir=support_dir,
                config_path=config_path,
                log_limit=input.log_limit,
                max_bytes=input.max_bytes,
            )
        )
        return SupportBundleReceiptView(**receipt.model_dump(mode="json"))

    @router.get("/support-bundles/{bundle_id}")
    async def management_support_bundle_download(
        bundle_id: str,
        actor: ManagementPrincipal = principal_dependency,
    ) -> FileResponse:
        authorize_management(actor, "logs.export")
        if not re.fullmatch(r"sup_[0-9a-f]{32}", bundle_id):
            raise RACPError("INVALID_ARGUMENT", "invalid support bundle identifier")
        path = support_dir / f"racp-support-{bundle_id}.zip"
        if not path.is_file():
            raise RACPError("NOT_FOUND", "support bundle was not found")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )

    return router
