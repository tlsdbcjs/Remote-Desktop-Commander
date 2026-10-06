import json

from racp_domain.models import RACPError
from racp_protocol.models import BrowserState
from racp_protocol.registry import REGISTRY

from racp_gateway.handles import HandleCatalog
from racp_gateway.store import GatewayStore


class BrowserEvents:
    def __init__(self, store: GatewayStore, handles: HandleCatalog) -> None:
        self.store, self.handles = store, handles
        store.db.execute("""CREATE TABLE IF NOT EXISTS browser_event_cursors (
            device_id TEXT NOT NULL, boot_id TEXT NOT NULL, provider_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, PRIMARY KEY(device_id,boot_id,provider_id))""")

    def accept(self, message: BrowserState) -> bool:
        owner = self.store.device(message.device_id)["owner_id"]
        for handle in message.handles:
            if (
                handle.provider_instance_id != message.provider_instance_id
                or handle.device_id != message.device_id
                or handle.agent_boot_id != message.agent_boot_id
                or handle.type not in {"browser", "browser-page"}
                or handle.owner != owner
            ):
                raise RACPError("PERMISSION_DENIED", "browser event Handle scope mismatch")
        if message.kind not in {"gap", "health"} and not any(
            h.id == message.browser_id and h.type == "browser" for h in message.handles
        ):
            raise RACPError("INVALID_ARGUMENT", "browser event requires its browser snapshot")
        row = self.store.db.execute(
            "SELECT sequence FROM browser_event_cursors "
            "WHERE device_id=? AND boot_id=? AND provider_id=?",
            (message.device_id, message.agent_boot_id, message.provider_instance_id),
        ).fetchone()
        sequence = int(message.event_sequence)
        if row and sequence <= row[0]:
            return False
        with self.store.transaction():
            if message.capability is not None:
                capability = message.capability
                if capability.name != "browser" or any(
                    name not in REGISTRY or REGISTRY[name].capability != "browser"
                    for name in capability.operations
                ):
                    raise RACPError("PERMISSION_DENIED", "browser capability scope mismatch")
                device = self.store.device(message.device_id)
                info = dict(device["info"])
                info["capabilities"] = [
                    c for c in info.get("capabilities", []) if c.get("name") != "browser"
                ] + [capability.model_dump()]
                self.store.db.execute(
                    "UPDATE devices SET info=? WHERE id=?", (json.dumps(info), message.device_id)
                )
            self.handles.observe(
                message.handles, message.device_id, message.agent_boot_id, complete=False
            )
            self.store.db.execute(
                "INSERT INTO browser_event_cursors VALUES (?,?,?,?) "
                "ON CONFLICT(device_id,boot_id,provider_id) "
                "DO UPDATE SET sequence=excluded.sequence",
                (message.device_id, message.agent_boot_id, message.provider_instance_id, sequence),
            )
            self.store.audit(
                "browser_state_changed",
                {"device_id": message.device_id, "context": {"principal_id": owner}},
                browser_id=message.browser_id,
                event_kind=message.kind,
                state=message.state,
                event_sequence=message.event_sequence,
            )
            if message.kind == "gap":
                self.store.audit(
                    "browser_gap",
                    {"device_id": message.device_id, "context": {"principal_id": owner}},
                )
        return True
