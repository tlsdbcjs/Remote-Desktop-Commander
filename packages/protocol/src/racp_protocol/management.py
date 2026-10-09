"""Shared models for Gateway management APIs."""

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from racp_protocol.models import Identifier, StrictModel


class GatewayPaths(StrictModel):
    release_root: Path
    config_file: Path
    state_root: Path
    database: Path
    secrets: Path
    logs: Path
    artifacts: Path
    backups: Path
    update_staging: Path


class ConfigCheck(StrictModel):
    valid: bool
    errors: list[str] = Field(default_factory=list, max_length=64)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    restart_required: bool = False


class ManagementPrincipal(StrictModel):
    actor_id: Identifier
    realm_id: Identifier = "realm_local"
    role: Literal["owner", "admin", "operator", "viewer"]
    device_grants: list[Identifier] = Field(default_factory=list, max_length=500)
    output_grants: list[Identifier] = Field(default_factory=list, max_length=500)
    operation_grants: list[str] = Field(default_factory=list, max_length=256)
    auth_revision: int = Field(ge=1)


class SettingsPatch(StrictModel):
    expected_revision: int = Field(ge=1)
    changes: dict[str, Any] = Field(max_length=32)


class SettingsView(StrictModel):
    revision: int = Field(ge=1)
    values: dict[str, Any]
    restart_required: bool = False


class GatewayStatusView(StrictModel):
    lifecycle: Literal["STARTING", "READY", "DRAINING", "STOPPING", "STOPPED", "DEGRADED", "FAILED"]
    ready: bool
    version: str = Field(max_length=64)
    instance_id: Identifier | None = None
    mode: Literal["service", "portable", "legacy"]
    setup_mode: Literal["local_owner", "oidc"]
    database: Literal["ready", "busy", "failed"]
    mcp: Literal["owner_bearer", "oauth", "not_configured"]
    settings_revision: int = Field(ge=1)
    connected_devices: int = Field(ge=0)
    total_devices: int = Field(ge=0)
    event_streams: int = Field(ge=0)
    tls_configured: bool = False
    tls_expires_at: str | None = Field(default=None, max_length=64)
    disk_free_bytes: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=64)


class MaintenanceJobView(StrictModel):
    id: Identifier
    kind: Literal["backup", "restore", "update"]
    state: Literal["PENDING", "RUNNING", "DEFERRED", "SUCCEEDED", "FAILED"]
    actor_id: Identifier
    realm_id: Identifier
    created_at: str
    updated_at: str
    progress: float | None = Field(default=None, ge=0, le=1)
    error: str | None = Field(default=None, max_length=2048)
    receipt_id: Identifier | None = None


class DrainReport(StrictModel):
    state: Literal["READY", "DEFERRED"]
    active_operations: list[Identifier] = Field(default_factory=list, max_length=500)
    active_handles: list[Identifier] = Field(default_factory=list, max_length=500)
    timeout_seconds: int = Field(ge=0, le=3600)


class BackupSetView(StrictModel):
    id: Identifier
    state: Literal["COMPLETE", "INCOMPLETE", "CORRUPT"]
    manifest_path: str = Field(max_length=4096)
    sha256: str = Field(min_length=64, max_length=64)
    schema_version: int = Field(ge=0)
    instance_id: Identifier
    created_at: str


class RestorePreview(StrictModel):
    backup_id: Identifier
    valid: bool
    schema_version: int = Field(ge=0)
    instance_id: Identifier
    database_integrity: Literal["ok", "failed"]
    warnings: list[str] = Field(default_factory=list, max_length=64)


class BackupCreateRequest(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")


class RestoreCreateRequest(StrictModel):
    backup_id: Identifier
    expected_instance_id: Identifier
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")
    confirm: Literal[True]


class UpdateStatusView(StrictModel):
    configured: bool
    current_version: str = Field(max_length=64)
    channel: str = Field(max_length=32)
    feed_configured: bool
    trust_key_configured: bool
    automatic_check: bool
    apply_enabled: bool
    reason: str | None = Field(default=None, max_length=512)


class UpdateCheckRequest(StrictModel):
    expected_revision: int = Field(ge=1)


class UpdateCandidateView(StrictModel):
    release_id: Identifier
    version: str = Field(max_length=64)
    platform: Literal["win-x64"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_rollback_compatible: bool
    checked_at: str = Field(max_length=64)


class UpdateApplyRequest(StrictModel):
    release_id: Identifier
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")
    confirm: Literal[True]


class SupportBundleCreateRequest(StrictModel):
    log_limit: int = Field(default=200, ge=1, le=500)
    max_bytes: int = Field(default=5 * 1024 * 1024, ge=4096, le=100 * 1024 * 1024)


class SupportBundleReceiptView(StrictModel):
    id: Identifier
    file_name: str = Field(max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    created_at: str = Field(max_length=64)
    log_entries: int = Field(ge=0)


class BootstrapReceipt(StrictModel):
    bootstrap_code: str = Field(min_length=20, max_length=128)
    expires_at: str = Field(max_length=64)
    expires_in_seconds: int = Field(default=300, ge=1, le=300)


class SetupRequest(StrictModel):
    bind_address: str = Field(max_length=255)
    port: int = Field(ge=1, le=65535)
    public_origin: str | None = Field(default=None, max_length=2048)
    certificate_file: str | None = Field(default=None, max_length=4096)
    private_key_file: str | None = Field(default=None, max_length=4096)
    client_ca_file: str | None = Field(default=None, max_length=4096)
    oauth_config_file: str | None = Field(default=None, max_length=4096)
    local_mcp_port: int | None = Field(default=None, ge=1, le=65535)


class DeviceQuery(StrictModel):
    search: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=32)
    group_id: Identifier | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)


class DeviceMetadataView(StrictModel):
    device_id: Identifier
    group_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    revision: int = Field(ge=1)


class DeviceGroupsPatch(StrictModel):
    group_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    expected_revision: int = Field(ge=1)


class DeviceGroupCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=100)


class DeviceGroupPatchRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    tags: list[str] | None = Field(default=None, max_length=100)
    device_ids: list[Identifier] | None = Field(default=None, max_length=500)


class DeviceGroupView(StrictModel):
    id: Identifier
    name: str = Field(max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=100)
    device_ids: list[Identifier] = Field(default_factory=list, max_length=500)
    revision: int = Field(ge=1)


class ManagedDeviceView(StrictModel):
    id: Identifier
    name: str
    revoked: bool
    epoch: int = Field(ge=0)
    info: dict[str, Any]
    group_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    revision: int = Field(ge=1)


class DevicePage(StrictModel):
    items: list[ManagedDeviceView]
    next_cursor: str | None = None


class ManagementUserView(StrictModel):
    id: Identifier
    realm_id: Identifier
    issuer: str = Field(max_length=2048)
    subject: str = Field(max_length=256)
    display_name: str = Field(max_length=256)
    role: Literal["owner", "admin", "operator", "viewer"]
    active: bool
    auth_revision: int = Field(ge=1)
    device_grants: list[Identifier] = Field(default_factory=list, max_length=500)
    output_grants: list[Identifier] = Field(default_factory=list, max_length=500)
    operation_grants: list[str] = Field(default_factory=list, max_length=256)


class ManagementUserCreateRequest(StrictModel):
    issuer: str = Field(min_length=1, max_length=2048)
    subject: str = Field(min_length=1, max_length=256)
    display_name: str = Field(max_length=256)
    role: Literal["owner", "admin", "operator", "viewer"]
    device_grants: list[Identifier] = Field(default_factory=list, max_length=500)
    output_grants: list[Identifier] = Field(default_factory=list, max_length=500)
    operation_grants: list[str] = Field(default_factory=list, max_length=256)


class ManagementUserPatchRequest(StrictModel):
    role: Literal["owner", "admin", "operator", "viewer"] | None = None
    active: bool | None = None
    device_grants: list[Identifier] | None = Field(default=None, max_length=500)
    output_grants: list[Identifier] | None = Field(default=None, max_length=500)
    operation_grants: list[str] | None = Field(default=None, max_length=256)


class OidcBeginView(StrictModel):
    authorization_url: str
    state: str = Field(min_length=20, max_length=256)
    expires_in_seconds: int = Field(default=300, ge=1, le=300)


class SafeLogEntry(StrictModel):
    timestamp: str = Field(max_length=64)
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    event: str = Field(min_length=1, max_length=128)
    message: str = Field(max_length=65536)
    device_id: Identifier | None = None
    request_id: str | None = Field(default=None, max_length=128)
    actor_id: Identifier | None = None
    fields: dict[str, Any] = Field(default_factory=dict, max_length=64)


class LogQuery(StrictModel):
    source: Literal["audit", "diagnostic"] | None = None
    device_id: Identifier | None = None
    request_id: str | None = Field(default=None, max_length=128)
    actor_id: Identifier | None = None
    from_utc: str | None = Field(default=None, max_length=64)
    to_utc: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)


class LogExportCreateRequest(StrictModel):
    query: LogQuery = Field(default_factory=LogQuery)
    format: Literal["json", "jsonl"] = "jsonl"
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[!-~]+$")
    max_rows: int = Field(default=10_000, ge=1, le=10_000)
    max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=5 * 1024 * 1024)


class LogExportReceiptView(StrictModel):
    id: Identifier
    state: Literal["PENDING", "SUCCEEDED", "FAILED"]
    format: Literal["json", "jsonl"]
    file_name: str = Field(max_length=255)
    rows: int = Field(ge=0, le=10_000)
    size_bytes: int = Field(ge=0, le=5 * 1024 * 1024)
    truncated: bool = False
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: str = Field(max_length=64)
    expires_at: str = Field(max_length=64)
    error: str | None = Field(default=None, max_length=512)


class LogItem(StrictModel):
    id: Identifier
    source: Literal["audit", "diagnostic"]
    timestamp: str
    event: str
    level: str
    message: str
    device_id: str
    request_id: str
    actor_id: str
    fields: dict[str, Any]


class LogPage(StrictModel):
    items: list[LogItem]
    next_cursor: str | None = None
    dropped_count: int = Field(default=0, ge=0)
