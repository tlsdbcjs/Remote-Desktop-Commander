import json
from contextlib import nullcontext
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import ResourceHandle, timestamp

from racp_gateway.store import GatewayStore


class HandleCatalog:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store
        store.db.execute("""CREATE TABLE IF NOT EXISTS handles (
            id TEXT PRIMARY KEY, device_id TEXT NOT NULL, owner_id TEXT NOT NULL,
            boot_id TEXT NOT NULL, record TEXT NOT NULL, observed_at TEXT NOT NULL)""")
        self.offline_all()

    def offline_all(self) -> None:
        self.store.db.execute(
            "UPDATE handles SET record=json_set(record,'$.availability','offline') "
            "WHERE json_extract(record,'$.state') IN ('ACTIVE','CREATING','CLOSING')"
        )

    def offline(self, device: str) -> None:
        self.store.db.execute(
            "UPDATE handles SET record=json_set(record,'$.availability','offline') "
            "WHERE device_id=? AND json_extract(record,'$.state') IN "
            "('ACTIVE','CREATING','CLOSING')",
            (device,),
        )

    def observe(
        self, handles: list[ResourceHandle], device: str, boot: str, *, complete: bool
    ) -> None:
        principal = self.store.device(device)["owner_id"]
        ids = set()
        for handle in handles:
            if (
                handle.device_id != device
                or handle.agent_boot_id != boot
                or handle.owner != principal
            ):
                raise RACPError("PERMISSION_DENIED", "resource inventory scope mismatch")
            if handle.id in ids:
                raise RACPError("INVALID_ARGUMENT", "duplicate inventory handle")
            ids.add(handle.id)
            previous = self.store.db.execute(
                "SELECT device_id,owner_id,boot_id FROM handles WHERE id=?", (handle.id,)
            ).fetchone()
            if previous and (
                previous["device_id"] != device
                or previous["owner_id"] != principal
                or previous["boot_id"] != boot
            ):
                raise RACPError("PERMISSION_DENIED", "handle identity is already bound")
        with nullcontext() if self.store.db.in_transaction else self.store.transaction():
            if complete:
                for row in self.store.db.execute(
                    "SELECT * FROM handles WHERE device_id=?", (device,)
                ).fetchall():
                    if row["id"] not in ids:
                        value = json.loads(row["record"])
                        if value["state"] in {"ACTIVE", "CREATING", "CLOSING"}:
                            value.update(state="EXPIRED", availability="unavailable")
                            value["resource_revision"] = str(int(value["resource_revision"]) + 1)
                            self.store.db.execute(
                                "UPDATE handles SET record=?,observed_at=? WHERE id=?",
                                (json.dumps(value), timestamp(), row["id"]),
                            )
            for handle in handles:
                existing = self.store.db.execute(
                    "SELECT record FROM handles WHERE id=?", (handle.id,)
                ).fetchone()
                if existing and int(json.loads(existing["record"])["resource_revision"]) > int(
                    handle.resource_revision
                ):
                    continue
                self.store.db.execute(
                    "INSERT INTO handles VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET record=excluded.record,"
                    "observed_at=excluded.observed_at",
                    (handle.id, device, principal, boot, handle.model_dump_json(), timestamp()),
                )

    def result(self, value: dict[str, Any] | None, device: str, boot: str) -> None:
        if value and isinstance(value.get("handle"), dict):
            handle = ResourceHandle.model_validate(value["handle"])
            # Journal replay preserves an earlier boot's historical operation
            # result. It does not revive that boot's volatile resource.
            if handle.agent_boot_id == boot:
                self.observe([handle], device, boot, complete=False)

    def get(self, id: str, owner: str) -> dict[str, Any]:
        row = self.store.db.execute(
            "SELECT record,observed_at FROM handles WHERE id=? AND owner_id=?", (id, owner)
        ).fetchone()
        if row is None:
            raise RACPError("HANDLE_EXPIRED", "handle was not found for this owner")
        return {**json.loads(row["record"]), "observed_at": row["observed_at"]}

    def list(self, device: str, owner: str) -> list[dict[str, Any]]:
        self.store.device(device, owner)
        return [
            self.get(row["id"], owner)
            for row in self.store.db.execute(
                "SELECT id FROM handles WHERE device_id=? AND owner_id=? "
                "ORDER BY observed_at DESC LIMIT 128",
                (device, owner),
            ).fetchall()
        ]
