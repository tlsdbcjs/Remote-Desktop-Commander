"""Management metadata and scoped views for many enrolled Agents."""

from __future__ import annotations

import builtins
import json
import sqlite3
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.management import (
    DeviceGroupView,
    DeviceMetadataView,
    DevicePage,
    DeviceQuery,
    ManagedDeviceView,
    ManagementPrincipal,
)
from racp_protocol.models import new_id, timestamp
from racp_sdk.pagination import CursorCodec
from racp_sdk.security import canonical_digest

from racp_gateway.management.authorization import authorize_management
from racp_gateway.store import GatewayStore


class DeviceManagement:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store
        self.cursor = CursorCodec()

    def _can_see(self, principal: ManagementPrincipal, device_id: str) -> bool:
        return principal.role in {"owner", "admin"} or device_id in principal.device_grants

    def _ensure_metadata(self, device_id: str, realm_id: str) -> None:
        self.store.db.execute(
            "INSERT OR IGNORE INTO device_management(device_id,realm_id,tags,revision,updated_at) "
            "VALUES (?,?, '[]',1,?)",
            (device_id, realm_id, timestamp()),
        )

    def create_group(
        self,
        principal: ManagementPrincipal,
        name: str,
        tags: builtins.list[str] | None = None,
    ) -> str:
        if principal.role not in {"owner", "admin"}:
            raise RACPError("PERMISSION_DENIED", "device group mutation requires admin role")
        if not name or len(name) > 128:
            raise RACPError("INVALID_ARGUMENT", "invalid device group name")
        if len(tags or []) != len(set(tags or [])) or len(tags or []) > 100:
            raise RACPError("INVALID_ARGUMENT", "invalid device group tags")
        group_id = new_id("grp")
        try:
            with self.store.transaction():
                self.store.db.execute(
                    "INSERT INTO device_groups(id,realm_id,name,tags,revision) VALUES (?,?,?,?,1)",
                    (group_id, principal.realm_id, name, json.dumps(tags or [])),
                )
                self.store.audit(
                    "device_group_created",
                    {"context": {"principal_id": principal.actor_id}},
                    group_id=group_id,
                )
        except sqlite3.IntegrityError as exc:
            raise RACPError("CONFLICT", "device group name already exists") from exc
        return group_id

    def groups(self, principal: ManagementPrincipal) -> builtins.list[DeviceGroupView]:
        authorize_management(principal, "devices.read")
        if principal.role in {"owner", "admin"}:
            rows = self.store.db.execute(
                "SELECT * FROM device_groups WHERE realm_id=? ORDER BY name,id",
                (principal.realm_id,),
            ).fetchall()
        elif principal.device_grants:
            marks = ",".join("?" for _ in principal.device_grants)
            rows = self.store.db.execute(
                "SELECT DISTINCT g.* FROM device_groups g "
                "JOIN device_group_members gm ON gm.group_id=g.id "
                f"WHERE g.realm_id=? AND gm.device_id IN ({marks}) ORDER BY g.name,g.id",
                (principal.realm_id, *principal.device_grants),
            ).fetchall()
        else:
            rows = []
        return [self._group_view(principal, row["id"]) for row in rows]

    def group(self, principal: ManagementPrincipal, group_id: str) -> DeviceGroupView:
        authorize_management(principal, "devices.read")
        return self._group_view(principal, group_id)

    def _group_view(self, principal: ManagementPrincipal, group_id: str) -> DeviceGroupView:
        row = self.store.db.execute(
            "SELECT * FROM device_groups WHERE id=? AND realm_id=?",
            (group_id, principal.realm_id),
        ).fetchone()
        if row is None:
            raise RACPError("NOT_FOUND", "device group was not found")
        device_ids = [
            str(item[0])
            for item in self.store.db.execute(
                "SELECT gm.device_id FROM device_group_members gm "
                "JOIN devices d ON d.id=gm.device_id "
                "WHERE gm.group_id=? ORDER BY gm.device_id LIMIT 500",
                (group_id,),
            ).fetchall()
        ]
        if principal.role not in {"owner", "admin"}:
            device_ids = [value for value in device_ids if value in principal.device_grants]
        return DeviceGroupView(
            id=str(row["id"]),
            name=str(row["name"]),
            tags=json.loads(row["tags"]),
            device_ids=device_ids,
            revision=int(row["revision"]),
        )

    def update_group(
        self,
        principal: ManagementPrincipal,
        group_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        tags: builtins.list[str] | None = None,
        device_ids: builtins.list[str] | None = None,
    ) -> DeviceGroupView:
        authorize_management(principal, "devices.write")
        if len(tags or []) != len(set(tags or [])) or len(tags or []) > 100:
            raise RACPError("INVALID_ARGUMENT", "invalid device group tags")
        if device_ids is not None:
            if len(device_ids) != len(set(device_ids)) or len(device_ids) > 500:
                raise RACPError("INVALID_ARGUMENT", "invalid device group members")
            found = {
                str(row[0])
                for row in self.store.db.execute(
                    "SELECT id FROM devices WHERE id IN ("
                    + ",".join("?" for _ in device_ids)
                    + ")",
                    device_ids,
                ).fetchall()
            } if device_ids else set()
            if found != set(device_ids):
                raise RACPError("NOT_FOUND", "one or more devices were not found")
        with self.store.transaction():
            current = self.store.db.execute(
                "SELECT * FROM device_groups WHERE id=? AND realm_id=?",
                (group_id, principal.realm_id),
            ).fetchone()
            if current is None:
                raise RACPError("NOT_FOUND", "device group was not found")
            if int(current["revision"]) != expected_revision:
                raise RACPError("CONFLICT", "device group revision changed")
            try:
                cursor = self.store.db.execute(
                    "UPDATE device_groups SET name=?,tags=?,revision=revision+1 "
                    "WHERE id=? AND realm_id=? AND revision=?",
                    (
                        name if name is not None else current["name"],
                        json.dumps(tags if tags is not None else json.loads(current["tags"])),
                        group_id,
                        principal.realm_id,
                        expected_revision,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RACPError("CONFLICT", "device group name already exists") from exc
            if cursor.rowcount != 1:
                raise RACPError("CONFLICT", "device group revision changed")
            if device_ids is not None:
                self.store.db.execute(
                    "DELETE FROM device_group_members WHERE group_id=?",
                    (group_id,),
                )
                self.store.db.executemany(
                    "INSERT INTO device_group_members(group_id,device_id) VALUES (?,?)",
                    [(group_id, device_id) for device_id in device_ids],
                )
            self.store.audit(
                "device_group_changed",
                {"context": {"principal_id": principal.actor_id}},
                group_id=group_id,
                revision=expected_revision + 1,
            )
        return self._group_view(principal, group_id)

    def list(self, principal: ManagementPrincipal, query: DeviceQuery) -> DevicePage:
        scope = canonical_digest(
            {
                "actor": principal.actor_id,
                "realm": principal.realm_id,
                "role": principal.role,
                "grants": sorted(principal.device_grants),
                "search": query.search,
                "status": query.status,
                "group": query.group_id,
                "limit": query.limit,
            }
        )
        before = (
            self.cursor.decode(query.cursor, scope, "management-devices-v1")
            if query.cursor
            else 2**63 - 1
        )
        clauses = ["d.rowid<?"]
        values: list[Any] = [before]
        if principal.role not in {"owner", "admin"}:
            if not principal.device_grants:
                return DevicePage(items=[])
            placeholders = ",".join("?" for _ in principal.device_grants)
            clauses.append(f"d.id IN ({placeholders})")
            values.extend(principal.device_grants)
        if query.search:
            clauses.append("(d.id LIKE ? ESCAPE '\\' OR d.name LIKE ? ESCAPE '\\')")
            escaped = query.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.extend([f"%{escaped}%", f"%{escaped}%"])
        if query.status:
            clauses.append("COALESCE(json_extract(d.info,'$.status'),'OFFLINE')=?")
            values.append(query.status)
        if query.group_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM device_group_members gm "
                "JOIN device_groups g ON g.id=gm.group_id "
                "WHERE gm.device_id=d.id AND gm.group_id=? AND g.realm_id=?)"
            )
            values.extend([query.group_id, principal.realm_id])
        rows = self.store.db.execute(
            "SELECT d.rowid AS _position,d.* FROM devices d WHERE "
            + " AND ".join(clauses)
            + " ORDER BY d.rowid DESC LIMIT ?",
            (*values, query.limit + 1),
        ).fetchall()
        items: list[ManagedDeviceView] = []
        for row in rows[: query.limit]:
            device_id = str(row["id"])
            self._ensure_metadata(device_id, principal.realm_id)
            metadata = self.store.db.execute(
                "SELECT tags,revision FROM device_management WHERE device_id=? AND realm_id=?",
                (device_id, principal.realm_id),
            ).fetchone()
            groups = [
                str(value[0])
                for value in self.store.db.execute(
                    "SELECT gm.group_id FROM device_group_members gm "
                    "JOIN device_groups g ON g.id=gm.group_id "
                    "WHERE gm.device_id=? AND g.realm_id=? ORDER BY gm.group_id",
                    (device_id, principal.realm_id),
                ).fetchall()
            ]
            assert metadata is not None
            items.append(
                ManagedDeviceView(
                    id=device_id,
                    name=str(row["name"]),
                    revoked=bool(row["revoked"]),
                    epoch=int(row["epoch"]),
                    info=json.loads(row["info"]),
                    group_ids=groups,
                    tags=json.loads(metadata["tags"]),
                    revision=int(metadata["revision"]),
                )
            )
        next_cursor = (
            self.cursor.encode(rows[query.limit - 1]["_position"], scope, "management-devices-v1")
            if len(rows) > query.limit
            else None
        )
        return DevicePage(items=items, next_cursor=next_cursor)

    def set_groups(
        self,
        principal: ManagementPrincipal,
        device_id: str,
        group_ids: builtins.list[str],
        expected_revision: int,
    ) -> DeviceMetadataView:
        if principal.role not in {"owner", "admin"}:
            raise RACPError("PERMISSION_DENIED", "device metadata mutation requires admin role")
        self.store.device(device_id)
        if len(group_ids) != len(set(group_ids)) or len(group_ids) > 100:
            raise RACPError("INVALID_ARGUMENT", "invalid device group set")
        with self.store.transaction():
            self._ensure_metadata(device_id, principal.realm_id)
            current = self.store.db.execute(
                "SELECT revision,tags FROM device_management WHERE device_id=? AND realm_id=?",
                (device_id, principal.realm_id),
            ).fetchone()
            assert current is not None
            if int(current["revision"]) != expected_revision:
                raise RACPError("CONFLICT", "device metadata revision changed")
            if group_ids:
                marks = ",".join("?" for _ in group_ids)
                found = {
                    str(row[0])
                    for row in self.store.db.execute(
                        f"SELECT id FROM device_groups "
                        f"WHERE realm_id=? AND id IN ({marks})",
                        (principal.realm_id, *group_ids),
                    ).fetchall()
                }
                if found != set(group_ids):
                    raise RACPError("PERMISSION_DENIED", "device group belongs to another realm")
            self.store.db.execute(
                "DELETE FROM device_group_members WHERE device_id=?", (device_id,)
            )
            self.store.db.executemany(
                "INSERT INTO device_group_members(group_id,device_id) VALUES (?,?)",
                [(group_id, device_id) for group_id in group_ids],
            )
            self.store.db.execute(
                "UPDATE device_management SET revision=revision+1,updated_at=? "
                "WHERE device_id=? AND realm_id=? AND revision=?",
                (timestamp(), device_id, principal.realm_id, expected_revision),
            )
            self.store.audit(
                "device_groups_changed",
                {"context": {"principal_id": principal.actor_id}, "device_id": device_id},
                group_count=len(group_ids),
            )
        return DeviceMetadataView(
            device_id=device_id,
            group_ids=sorted(group_ids),
            tags=json.loads(current["tags"]),
            revision=expected_revision + 1,
        )
