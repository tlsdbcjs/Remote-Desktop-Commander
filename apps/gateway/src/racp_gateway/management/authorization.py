"""Server-side management role and scope authorization."""

from typing import Literal

from racp_domain.models import RACPError
from racp_protocol.management import ManagementPrincipal

ManagementAction = Literal[
    "status.read",
    "settings.read",
    "settings.write",
    "devices.read",
    "devices.write",
    "operations.execute",
    "logs.read",
    "logs.export",
    "users.read",
    "users.write",
    "backups.read",
    "backups.write",
    "updates.read",
    "updates.write",
    "artifacts.read",
]

READ_ACTIONS = {
    "status.read",
    "settings.read",
    "devices.read",
    "logs.read",
    "users.read",
    "backups.read",
    "updates.read",
}
OPERATOR_ACTIONS = READ_ACTIONS | {
    "operations.execute",
    "logs.export",
    "backups.write",
    "artifacts.read",
}
ADMIN_ACTIONS = OPERATOR_ACTIONS | {
    "settings.write",
    "devices.write",
    "updates.write",
    "artifacts.read",
}


def authorize_management(
    principal: ManagementPrincipal,
    action: ManagementAction,
    device_id: str | None = None,
    *,
    output_id: str | None = None,
    operation: str | None = None,
) -> None:
    allowed = (
        True
        if principal.role == "owner"
        else action in ADMIN_ACTIONS
        if principal.role == "admin"
        else action in OPERATOR_ACTIONS
        if principal.role == "operator"
        else action in READ_ACTIONS
    )
    if not allowed:
        raise RACPError("PERMISSION_DENIED", "management role does not allow this action")
    if device_id and principal.role not in {"owner", "admin"}:
        if device_id not in principal.device_grants:
            raise RACPError("PERMISSION_DENIED", "device is outside the management grant")
    if output_id and principal.role not in {"owner", "admin"}:
        if output_id not in principal.output_grants:
            raise RACPError("PERMISSION_DENIED", "output is outside the management grant")
    if action == "operations.execute" and operation and principal.role not in {"owner", "admin"}:
        if operation not in principal.operation_grants:
            raise RACPError("PERMISSION_DENIED", "operation is outside the management grant")
