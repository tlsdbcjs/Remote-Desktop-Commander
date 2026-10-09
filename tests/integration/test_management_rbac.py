from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.console_auth import ConsoleAuth
from racp_gateway.management.auth import ManagementAuth
from racp_gateway.management.authorization import authorize_management
from racp_gateway.management.users import UserManagement
from racp_gateway.store import GatewayStore
from racp_protocol.management import ManagementPrincipal
from racp_sdk.security import digest


def owner() -> ManagementPrincipal:
    return ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)


def test_viewer_mutation_operator_device_scope_and_artifact_grant(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    try:
        viewer = ManagementPrincipal(actor_id="viewer", role="viewer", auth_revision=1)
        with pytest.raises(RACPError):
            authorize_management(viewer, "settings.write")
        admin = ManagementPrincipal(actor_id="admin", role="admin", auth_revision=1)
        with pytest.raises(RACPError):
            authorize_management(admin, "users.write")
        operator = ManagementPrincipal(
            actor_id="operator",
            role="operator",
            device_grants=["dev_allowed"],
            output_grants=["art_allowed"],
            operation_grants=["filesystem.stat"],
            auth_revision=1,
        )
        authorize_management(operator, "devices.read", "dev_allowed")
        authorize_management(
            operator,
            "operations.execute",
            "dev_allowed",
            operation="filesystem.stat",
        )
        authorize_management(operator, "artifacts.read", "dev_allowed", output_id="art_allowed")
        for action in [
            lambda: authorize_management(operator, "devices.read", "dev_other"),
            lambda: authorize_management(
                operator, "operations.execute", "dev_allowed", operation="shell.execute"
            ),
            lambda: authorize_management(
                operator, "artifacts.read", "dev_allowed", output_id="art_other"
            ),
        ]:
            with pytest.raises(RACPError) as denied:
                action()
            assert denied.value.error.code == "PERMISSION_DENIED"
    finally:
        store.close()


def test_last_owner_denied_and_role_revision_rechecked_on_existing_session(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    users = UserManagement(store)
    auth = ConsoleAuth(store)
    try:
        managed_owner = users.create(
            owner(), issuer="https://idp.example", subject="one", display_name="One", role="owner"
        )
        with pytest.raises(RACPError) as last_owner:
            users.deactivate(owner(), managed_owner.id)
        assert last_owner.value.error.code == "CONFLICT"

        operator = users.create(
            owner(),
            issuer="https://idp.example",
            subject="operator",
            display_name="Operator",
            role="operator",
            device_grants=["dev_one"],
        )
        credential, _ = auth.create_session(operator.id)
        resolver = ManagementAuth(auth, users)
        before = resolver.resolve_management_principal(credential)
        assert before.role == "operator" and before.auth_revision == 1
        users.update_role(owner(), operator.id, "viewer")
        after = resolver.resolve_management_principal(credential)
        assert after.role == "viewer" and after.auth_revision == 2
    finally:
        store.close()
