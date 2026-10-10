"""First-run management setup with revisioned config commits."""

from pathlib import Path

from racp_domain.models import RACPError
from racp_protocol.management import ConfigCheck, ManagementPrincipal, SettingsView, SetupRequest

from racp_gateway.config import (
    GatewayConfig,
    GatewayTlsConfig,
    load_gateway_config,
    validate_gateway_config,
    write_gateway_config,
)
from racp_gateway.console_auth import ConsoleAuth
from racp_gateway.local_admin import LocalAdminBroker
from racp_gateway.store import GatewayStore


class SetupService:
    def __init__(self, config_path: Path, store: GatewayStore, console_auth: ConsoleAuth) -> None:
        self.config_path = config_path
        self.store = store
        self.console_auth = console_auth

    def _candidate(self, request: SetupRequest) -> tuple[GatewayConfig, GatewayConfig]:
        current = load_gateway_config(self.config_path)
        candidate = current.model_copy(
            update={
                "bind_address": request.bind_address,
                "port": request.port,
                "public_origin": request.public_origin,
                "tls": GatewayTlsConfig(
                    certificate_file=request.certificate_file,
                    private_key_file=request.private_key_file,
                    client_ca_file=request.client_ca_file,
                ),
                "auth": current.auth.model_copy(
                    update={
                        "oauth_config_file": request.oauth_config_file,
                        "local_mcp_port": request.local_mcp_port,
                    }
                ),
            }
        )
        return current, candidate

    def preview(self, input: SetupRequest) -> ConfigCheck:
        current, candidate = self._candidate(input)
        check = validate_gateway_config(candidate, check_listener=True)
        restart = any(
            getattr(current, field) != getattr(candidate, field)
            for field in ("bind_address", "port", "public_origin", "tls", "auth")
        )
        return check.model_copy(update={"restart_required": restart})

    def commit(
        self,
        input: SetupRequest,
        expected_revision: int,
        principal: ManagementPrincipal,
    ) -> SettingsView:
        if principal.role != "owner":
            raise RACPError("PERMISSION_DENIED", "only the owner can complete Gateway setup")
        current, candidate = self._candidate(input)
        if current.revision != expected_revision:
            raise RACPError("CONFLICT", "Gateway configuration revision changed")
        check = validate_gateway_config(candidate, check_listener=True)
        if not check.valid:
            raise RACPError(
                "INVALID_ARGUMENT", "Gateway setup validation failed", errors=check.errors
            )
        committed = candidate.model_copy(update={"revision": expected_revision + 1})
        write_gateway_config(self.config_path, committed, expected_revision=expected_revision)
        self.store.audit(
            "gateway_setup_committed",
            {"context": {"principal_id": principal.actor_id}},
            revision=committed.revision,
            restart_required=check.restart_required,
        )
        return SettingsView(
            revision=committed.revision,
            values={
                "bind_address": committed.bind_address,
                "port": committed.port,
                "public_origin": committed.public_origin,
                "tls_configured": bool(committed.tls.certificate_file),
                "oauth_configured": bool(committed.auth.oauth_config_file),
                "local_mcp_port": committed.auth.local_mcp_port,
            },
            restart_required=True,
        )

    def exchange_bootstrap(self, code: str) -> dict[str, object]:
        LocalAdminBroker(self.store).consume_bootstrap(code)
        return self.console_auth.setup("owner_local")
