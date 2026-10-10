"""Management user identities, roles and immediately revisioned grants."""

from __future__ import annotations

import builtins
import json
import sqlite3

from racp_domain.models import RACPError
from racp_protocol.management import ManagementPrincipal, ManagementUserView
from racp_protocol.models import new_id, timestamp

from racp_gateway.management.authorization import authorize_management
from racp_gateway.store import GatewayStore


class UserManagement:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def _view(self, row: sqlite3.Row) -> ManagementUserView:
        value = dict(row)
        binding = self.store.db.execute(
            "SELECT * FROM role_bindings WHERE user_id=?", (value["id"],)
        ).fetchone()
        if binding is None:
            raise RuntimeError("management user is missing a role binding")
        return ManagementUserView(
            id=value["id"],
            realm_id=value["realm_id"],
            issuer=value["issuer"],
            subject=value["subject"],
            display_name=value["display_name"],
            role=binding["role"],
            active=bool(value["active"]),
            auth_revision=int(value["auth_revision"]),
            device_grants=json.loads(binding["device_grants"]),
            output_grants=json.loads(binding["output_grants"]),
            operation_grants=json.loads(binding["operation_grants"]),
        )

    def create(
        self,
        principal: ManagementPrincipal,
        *,
        issuer: str,
        subject: str,
        display_name: str,
        role: str,
        device_grants: builtins.list[str] | None = None,
        output_grants: builtins.list[str] | None = None,
        operation_grants: builtins.list[str] | None = None,
    ) -> ManagementUserView:
        authorize_management(principal, "users.write")
        if role not in {"owner", "admin", "operator", "viewer"}:
            raise RACPError("INVALID_ARGUMENT", "unknown management role")
        user_id = new_id("usr")
        now = timestamp()
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO management_users("
                "id,realm_id,issuer,subject,display_name,active,auth_revision,created_at"
                ") "
                "VALUES (?,?,?,?,?,1,1,?)",
                (user_id, principal.realm_id, issuer, subject, display_name, now),
            )
            self.store.db.execute(
                "INSERT INTO role_bindings("
                "user_id,role,device_grants,output_grants,operation_grants,updated_at"
                ") "
                "VALUES (?,?,?,?,?,?)",
                (
                    user_id,
                    role,
                    json.dumps(device_grants or []),
                    json.dumps(output_grants or []),
                    json.dumps(operation_grants or []),
                    now,
                ),
            )
            self.store.audit(
                "management_user_created",
                {"context": {"principal_id": principal.actor_id}},
                user_id=user_id,
                role=role,
            )
        row = self.store.db.execute(
            "SELECT * FROM management_users WHERE id=?", (user_id,)
        ).fetchone()
        assert row is not None
        return self._view(row)

    def list(self, principal: ManagementPrincipal) -> builtins.list[ManagementUserView]:
        authorize_management(principal, "users.read")
        return [
            self._view(row)
            for row in self.store.db.execute(
                "SELECT * FROM management_users WHERE realm_id=? ORDER BY id",
                (principal.realm_id,),
            ).fetchall()
        ]

    def get(self, principal: ManagementPrincipal, user_id: str) -> ManagementUserView:
        authorize_management(principal, "users.read")
        row = self.store.db.execute(
            "SELECT * FROM management_users WHERE id=? AND realm_id=?",
            (user_id, principal.realm_id),
        ).fetchone()
        if row is None:
            raise RACPError("NOT_FOUND", "management user was not found")
        return self._view(row)

    def patch(
        self,
        principal: ManagementPrincipal,
        user_id: str,
        *,
        role: str | None,
        active: bool | None,
        device_grants: builtins.list[str] | None,
        output_grants: builtins.list[str] | None,
        operation_grants: builtins.list[str] | None,
    ) -> ManagementUserView:
        current = self.get(principal, user_id)
        if active is False:
            self.deactivate(principal, user_id)
            return self.get(principal, user_id)
        if active is True and not current.active:
            raise RACPError("INVALID_ARGUMENT", "disabled management users cannot be reactivated")
        return self.update_role(
            principal,
            user_id,
            role or current.role,
            device_grants=current.device_grants if device_grants is None else device_grants,
            output_grants=current.output_grants if output_grants is None else output_grants,
            operation_grants=(
                current.operation_grants if operation_grants is None else operation_grants
            ),
        )

    def update_role(
        self,
        principal: ManagementPrincipal,
        user_id: str,
        role: str,
        *,
        device_grants: builtins.list[str] | None = None,
        output_grants: builtins.list[str] | None = None,
        operation_grants: builtins.list[str] | None = None,
    ) -> ManagementUserView:
        authorize_management(principal, "users.write")
        if role not in {"owner", "admin", "operator", "viewer"}:
            raise RACPError("INVALID_ARGUMENT", "unknown management role")
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT * FROM management_users WHERE id=? AND realm_id=? AND active=1",
                (user_id, principal.realm_id),
            ).fetchone()
            if row is None:
                raise RACPError("NOT_FOUND", "management user was not found")
            old = self.store.db.execute(
                "SELECT role FROM role_bindings WHERE user_id=?", (user_id,)
            ).fetchone()
            if old and old["role"] == "owner" and role != "owner":
                owners = self.store.db.execute(
                    "SELECT COUNT(*) FROM management_users u "
                    "JOIN role_bindings r ON r.user_id=u.id "
                    "WHERE u.realm_id=? AND u.active=1 AND r.role='owner'",
                    (principal.realm_id,),
                ).fetchone()[0]
                if owners <= 1:
                    raise RACPError("CONFLICT", "the last management owner cannot be demoted")
            self.store.db.execute(
                "UPDATE role_bindings SET "
                "role=?,device_grants=?,output_grants=?,operation_grants=?,updated_at=? "
                "WHERE user_id=?",
                (
                    role,
                    json.dumps(device_grants or []),
                    json.dumps(output_grants or []),
                    json.dumps(operation_grants or []),
                    timestamp(),
                    user_id,
                ),
            )
            self.store.db.execute(
                "UPDATE management_users SET auth_revision=auth_revision+1 WHERE id=?", (user_id,)
            )
            self.store.audit(
                "management_user_role_changed",
                {"context": {"principal_id": principal.actor_id}},
                user_id=user_id,
                role=role,
            )
        updated = self.store.db.execute(
            "SELECT * FROM management_users WHERE id=?", (user_id,)
        ).fetchone()
        assert updated is not None
        return self._view(updated)

    def deactivate(self, principal: ManagementPrincipal, user_id: str) -> None:
        authorize_management(principal, "users.write")
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT u.*,r.role FROM management_users u JOIN role_bindings r ON r.user_id=u.id "
                "WHERE u.id=? AND u.realm_id=? AND u.active=1",
                (user_id, principal.realm_id),
            ).fetchone()
            if row is None:
                raise RACPError("NOT_FOUND", "management user was not found")
            if row["role"] == "owner":
                owners = self.store.db.execute(
                    "SELECT COUNT(*) FROM management_users u "
                    "JOIN role_bindings r ON r.user_id=u.id "
                    "WHERE u.realm_id=? AND u.active=1 AND r.role='owner'",
                    (principal.realm_id,),
                ).fetchone()[0]
                if owners <= 1:
                    raise RACPError("CONFLICT", "the last management owner cannot be removed")
            self.store.db.execute(
                "UPDATE management_users SET active=0,auth_revision=auth_revision+1 WHERE id=?",
                (user_id,),
            )
            self.store.audit(
                "management_user_disabled",
                {"context": {"principal_id": principal.actor_id}},
                user_id=user_id,
            )

    def principal_for_identity(self, issuer: str, subject: str) -> ManagementPrincipal:
        row = self.store.db.execute(
            "SELECT u.*,r.role,r.device_grants,r.output_grants,r.operation_grants "
            "FROM management_users u JOIN role_bindings r ON r.user_id=u.id "
            "WHERE u.issuer=? AND u.subject=? AND u.active=1",
            (issuer, subject),
        ).fetchone()
        if row is None:
            raise RACPError("PERMISSION_DENIED", "OIDC identity is not registered")
        return ManagementPrincipal(
            actor_id=row["id"],
            realm_id=row["realm_id"],
            role=row["role"],
            device_grants=json.loads(row["device_grants"]),
            output_grants=json.loads(row["output_grants"]),
            operation_grants=json.loads(row["operation_grants"]),
            auth_revision=int(row["auth_revision"]),
        )
