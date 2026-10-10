import json
import time
from typing import TYPE_CHECKING, Any

from racp_domain.jobs import OPERATION_JOB_STATE
from racp_domain.models import RACPError
from racp_protocol.models import new_id, timestamp
from racp_sdk.journal import Journal
from racp_sdk.security import digest, token

if TYPE_CHECKING:
    from racp_gateway.events import EventFeed

from racp_gateway.migrations import CURRENT_GATEWAY_SCHEMA, migrate_gateway


class GatewayStore(Journal):
    event_feed: "EventFeed | None" = None

    def audit(self, event: str, request: dict[str, Any], **summary: Any) -> None:
        super().audit(event, request, **summary)
        if self.event_feed is not None:
            owner = request.get("context", {}).get("principal_id", "owner_local")
            self.event_feed.publish(owner, event, request)

    def initialize(self, owner_digest: str | None = None) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS owner (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS enrollments (
              digest TEXT PRIMARY KEY, name TEXT NOT NULL, expires REAL NOT NULL,
              used INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS devices (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL,
              revoked INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0,
              info TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS credentials (
              digest TEXT PRIMARY KEY, device_id TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
              expires REAL NOT NULL, state TEXT NOT NULL);
        """)
        if owner_digest:
            with self.transaction():
                if self.db.execute("SELECT 1 FROM owner").fetchone():
                    raise RACPError("CONFLICT", "owner is already initialized")
                self.db.execute("INSERT INTO owner VALUES ('owner_local', ?)", (owner_digest,))
        migrate_gateway(self.db, CURRENT_GATEWAY_SCHEMA)
        self.db.execute("UPDATE devices SET info=json_set(info,'$.status','OFFLINE')")

    def owner(self, bearer: str) -> str:
        row = self.db.execute("SELECT id FROM owner WHERE digest=?", (digest(bearer),)).fetchone()
        if not row:
            raise RACPError("UNAUTHENTICATED", "owner authentication required")
        return str(row["id"])

    def enrollment(self, name: str, ttl_seconds: int = 600) -> str:
        secret = token()
        with self.transaction():
            self.db.execute(
                "INSERT INTO enrollments VALUES (?,?,?,0)",
                (digest(secret), name, time.time() + ttl_seconds),
            )
            self.audit("enrollment_created", {}, name=name)
        return secret

    def enroll(self, secret: str) -> dict[str, str]:
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM enrollments WHERE digest=?", (digest(secret),)
            ).fetchone()
            if not row or row["used"] or row["expires"] <= time.time():
                raise RACPError("UNAUTHENTICATED", "invalid or consumed enrollment")
            owner = self.db.execute("SELECT id FROM owner").fetchone()
            if not owner:
                raise RACPError("UNAUTHENTICATED", "owner is not initialized")
            device_id, credential = new_id("dev"), token()
            self.db.execute("UPDATE enrollments SET used=1 WHERE digest=?", (digest(secret),))
            self.db.execute(
                "INSERT INTO devices(id,name,owner_id,info) VALUES (?,?,?,?)",
                (
                    device_id,
                    row["name"],
                    owner["id"],
                    json.dumps({"status": "OFFLINE", "last_observed_at": timestamp()}),
                ),
            )
            self.db.execute(
                "INSERT INTO credentials VALUES (?,?,?)",
                (digest(credential), device_id, time.time() + 90 * 86400),
            )
            self.audit("device_enrolled", {"device_id": device_id})
        return {"device_id": device_id, "credential": credential}

    def authenticate_device(self, credential: str) -> str:
        row = self.db.execute(
            """SELECT c.device_id,d.revoked,c.expires FROM credentials c
            JOIN devices d ON d.id=c.device_id WHERE c.digest=?""",
            (digest(credential),),
        ).fetchone()
        if not row or row["expires"] <= time.time():
            raise RACPError("UNAUTHENTICATED", "device authentication required")
        if row["revoked"]:
            raise RACPError("DEVICE_REVOKED", "device was revoked")
        return str(row["device_id"])

    def device(self, device_id: str, owner: str = "owner_local") -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM devices WHERE id=? AND owner_id=?", (device_id, owner)
        ).fetchone()
        if not row:
            raise RACPError("PERMISSION_DENIED", "device is not owned by this principal")
        return {**dict(row), "info": json.loads(row["info"])}

    def devices(self, owner: str) -> list[dict[str, Any]]:
        return [
            self.device(str(row[0]), owner)
            for row in self.db.execute(
                "SELECT id FROM devices WHERE owner_id=? ORDER BY id LIMIT 500", (owner,)
            ).fetchall()
        ]

    def connected(self, device_id: str, info: dict[str, Any]) -> int:
        with self.transaction():
            device = self.device(device_id)
            if device["revoked"]:
                raise RACPError("DEVICE_REVOKED", "device was revoked")
            epoch = int(device["epoch"]) + 1
            self.db.execute(
                "UPDATE devices SET epoch=?,info=? WHERE id=?",
                (
                    epoch,
                    json.dumps(
                        {**info, "last_seen_at": timestamp(), "last_observed_at": timestamp()}
                    ),
                    device_id,
                ),
            )
            self.audit("device_connecting", {"device_id": device_id})
        return epoch

    def device_status(self, device_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE devices SET info=json_set(info,'$.status',?,'$.last_observed_at',?) WHERE id=?",
            (status, timestamp(), device_id),
        )
        self.audit("device_status_changed", {"device_id": device_id}, status=status)

    def device_heartbeat(self, device_id: str, health: str) -> None:
        with self.transaction():
            self.db.execute(
                "UPDATE devices SET info=json_set(info,'$.health',?,'$.last_seen_at',?) WHERE id=?",
                (health, timestamp(), device_id),
            )
            self.device_status(device_id, "ONLINE" if health == "healthy" else "DEGRADED")

    def revoke(self, device_id: str) -> None:
        with self.transaction():
            self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
            self.device_status(device_id, "REVOKED")
            self.audit("device_revoked", {"device_id": device_id})

    def rotate(self, device_id: str) -> str:
        credential = token()
        with self.transaction():
            self.db.execute(
                "UPDATE credentials SET expires=min(expires,?) WHERE device_id=?",
                (time.time() + 600, device_id),
            )
            self.db.execute(
                "INSERT INTO credentials VALUES (?,?,?)",
                (digest(credential), device_id, time.time() + 90 * 86400),
            )
            self.audit("credential_rotated", {"device_id": device_id})
        return credential

    def approval(self, operation_id: str, payload_digest: str) -> str:
        row = self.db.execute(
            "SELECT id FROM approvals WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row:
            return str(row[0])
        approval_id = new_id("apr")
        self.db.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?)",
            (approval_id, operation_id, payload_digest, time.time() + 300, "PENDING"),
        )
        self.audit("approval_requested", self.get(operation_id)["request"], approval_id=approval_id)
        return approval_id

    def approval_record(self, approval_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise RACPError("PERMISSION_DENIED", "approval was not found")
        record = dict(row)
        if record["expires"] <= time.time() and record["state"] in {"PENDING", "APPROVED"}:
            self.db.execute("UPDATE approvals SET state='EXPIRED' WHERE id=?", (approval_id,))
            record["state"] = "EXPIRED"
        return record

    def decide_approval(self, approval_id: str, approve: bool) -> dict[str, Any]:
        with self.transaction():
            record = self.approval_record(approval_id)
            if record["state"] != "PENDING":
                raise RACPError("CONFLICT", "approval is not pending")
            self.db.execute(
                "UPDATE approvals SET state=? WHERE id=?",
                ("APPROVED" if approve else "DENIED", approval_id),
            )
            self.audit(
                "approval_decided",
                self.get(record["operation_id"])["request"],
                approval_id=approval_id,
                approved=approve,
            )
        return self.approval_record(approval_id)

    def claim_approval(self, approval_id: str, operation_id: str, payload_digest: str) -> None:
        with self.transaction():
            self.consume_approval(approval_id, operation_id, payload_digest)

    def consume_approval(self, approval_id: str, operation_id: str, payload_digest: str) -> None:
        record = self.approval_record(approval_id)
        if record["expires"] <= time.time():
            raise RACPError("APPROVAL_EXPIRED", "approval expired")
        if (
            record["state"] != "APPROVED"
            or record["operation_id"] != operation_id
            or record["digest"] != payload_digest
        ):
            raise RACPError("PERMISSION_DENIED", "approval does not match this operation")
        self.db.execute("UPDATE approvals SET state='CONSUMED' WHERE id=?", (approval_id,))
        self.audit("approval_consumed", self.get(operation_id)["request"], approval_id=approval_id)

    def on_transition(self, previous: dict[str, Any], state: str) -> None:
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
            return
        job = self.db.execute(
            "SELECT state FROM jobs WHERE operation_id=?", (previous["id"],)
        ).fetchone()
        if job:
            self.db.execute(
                "UPDATE jobs SET state=?,waiting_reason=NULL,revision=revision+1,updated_at=? "
                "WHERE operation_id=?",
                (OPERATION_JOB_STATE[state], timestamp(), previous["id"]),
            )
        if state in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "UNKNOWN"}:
            self.db.execute(
                "UPDATE operation_admission SET state='DONE' WHERE operation_id=?",
                (previous["id"],),
            )
