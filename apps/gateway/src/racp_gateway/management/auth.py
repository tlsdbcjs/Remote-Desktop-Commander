"""Resolve local Console and registered management identities on every request."""

from racp_domain.models import RACPError
from racp_protocol.management import ManagementPrincipal

from racp_gateway.console_auth import ConsoleAuth
from racp_gateway.management.users import UserManagement


class ManagementAuth:
    def __init__(self, console_auth: ConsoleAuth, users: UserManagement) -> None:
        self.console_auth = console_auth
        self.users = users

    def resolve_management_principal(
        self,
        session: str,
        *,
        csrf: str | None = None,
        touch: bool = False,
    ) -> ManagementPrincipal:
        actor = self.console_auth.authenticate(session, csrf=csrf, touch=touch)
        if actor == "owner_local":
            return ManagementPrincipal(actor_id=actor, role="owner", auth_revision=1)
        row = self.users.store.db.execute(
            "SELECT u.issuer,u.subject,u.auth_revision,u.active "
            "FROM management_users u WHERE u.id=?",
            (actor,),
        ).fetchone()
        if row is None or not row["active"]:
            raise RACPError("SESSION_EXPIRED", "management identity is no longer active")
        return self.users.principal_for_identity(str(row["issuer"]), str(row["subject"]))
