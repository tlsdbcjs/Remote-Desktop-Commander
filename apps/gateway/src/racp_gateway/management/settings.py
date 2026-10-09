"""Revisioned management settings validation without exposing secret values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.management import ConfigCheck, ManagementPrincipal, SettingsPatch, SettingsView

from racp_gateway.config import (
    GatewayConfig,
    load_gateway_config,
    validate_gateway_config,
    write_gateway_config,
)
from racp_gateway.management.authorization import authorize_management
from racp_gateway.management.store import ManagementStore

_ALLOWED_TOP_LEVEL = {
    "bind_address",
    "port",
    "public_origin",
    "retention",
    "update",
}
_SECRET_KEYS = {
    "certificate_file",
    "private_key_file",
    "client_ca_file",
    "oauth_config_file",
    "trust_key_file",
}


def _public_config(config: GatewayConfig) -> dict[str, Any]:
    value = config.model_dump(mode="json")
    value["tls"] = {
        "configured": bool(config.tls.certificate_file and config.tls.private_key_file),
        "client_ca_configured": bool(config.tls.client_ca_file),
    }
    value["auth"] = {
        "oauth_configured": bool(config.auth.oauth_config_file),
        "local_mcp_port": config.auth.local_mcp_port,
    }
    update = dict(value["update"])
    update["trust_key_configured"] = bool(config.update.trust_key_file)
    update.pop("trust_key_file", None)
    value["update"] = update
    value.pop("state_root", None)
    return value


class SettingsService:
    def __init__(self, path: Path, management: ManagementStore) -> None:
        self.path = path
        self.management = management

    def view(self, principal: ManagementPrincipal) -> SettingsView:
        authorize_management(principal, "settings.read")
        config = load_gateway_config(self.path)
        return SettingsView(revision=config.revision, values=_public_config(config))

    def _candidate(self, patch: SettingsPatch) -> tuple[GatewayConfig, GatewayConfig, ConfigCheck]:
        config = load_gateway_config(self.path)
        if config.revision != patch.expected_revision:
            raise RACPError("CONFLICT", "Gateway configuration revision changed")
        unknown = set(patch.changes) - _ALLOWED_TOP_LEVEL
        if unknown:
            raise RACPError(
                "INVALID_ARGUMENT",
                "settings patch contains unsupported fields",
                fields=sorted(unknown),
            )
        serialized = config.model_dump(mode="json")
        for key, value in patch.changes.items():
            if isinstance(value, dict) and any(secret in value for secret in _SECRET_KEYS):
                raise RACPError(
                    "INVALID_ARGUMENT",
                    "secret references are changed through the setup flow",
                )
            serialized[key] = value
        candidate = GatewayConfig.model_validate(serialized)
        listener_changed = (
            candidate.bind_address != config.bind_address or candidate.port != config.port
        )
        check = validate_gateway_config(candidate, check_listener=listener_changed)
        restart_required = any(
            key in patch.changes for key in {"bind_address", "port", "public_origin"}
        )
        return config, candidate, check.model_copy(update={"restart_required": restart_required})

    def stage(self, principal: ManagementPrincipal, patch: SettingsPatch) -> ConfigCheck:
        authorize_management(principal, "settings.write")
        _, _, check = self._candidate(patch)
        return check

    def apply(self, principal: ManagementPrincipal, patch: SettingsPatch) -> SettingsView:
        authorize_management(principal, "settings.write")
        current, candidate, check = self._candidate(patch)
        if not check.valid:
            raise RACPError(
                "INVALID_ARGUMENT",
                "Gateway settings validation failed",
                errors=check.errors,
            )
        committed = candidate.model_copy(update={"revision": current.revision + 1})
        try:
            with self.management.store.transaction():
                self.management.store.audit(
                    "gateway_settings_committed",
                    {"context": {"principal_id": principal.actor_id}},
                    revision=committed.revision,
                    changed_fields=sorted(patch.changes),
                    restart_required=check.restart_required,
                )
                write_gateway_config(
                    self.path,
                    committed,
                    expected_revision=current.revision,
                )
        except ValueError as exc:
            if "revision conflict" in str(exc).lower():
                raise RACPError("CONFLICT", "Gateway configuration revision changed") from exc
            raise
        return SettingsView(
            revision=committed.revision,
            values=_public_config(committed),
            restart_required=check.restart_required,
        )
