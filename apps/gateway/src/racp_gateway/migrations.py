"""Gateway-only schema migrations layered above the shared Journal schema."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from racp_protocol.models import timestamp

CURRENT_GATEWAY_SCHEMA = 2

MIGRATION_1 = (
    "CREATE TABLE IF NOT EXISTS gateway_settings ("
    "id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, settings_json TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS management_users ("
    "id TEXT PRIMARY KEY, realm_id TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL, "
    "display_name TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, "
    "auth_revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(issuer,subject))",
    "CREATE TABLE IF NOT EXISTS role_bindings ("
    "user_id TEXT PRIMARY KEY, role TEXT NOT NULL, device_grants TEXT NOT NULL DEFAULT '[]', "
    "output_grants TEXT NOT NULL DEFAULT '[]', operation_grants TEXT NOT NULL DEFAULT '[]', "
    "updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS device_groups ("
    "id TEXT PRIMARY KEY, realm_id TEXT NOT NULL, name TEXT NOT NULL, "
    "tags TEXT NOT NULL DEFAULT '[]', "
    "revision INTEGER NOT NULL DEFAULT 1, UNIQUE(realm_id,name))",
    "CREATE TABLE IF NOT EXISTS device_group_members ("
    "group_id TEXT NOT NULL, device_id TEXT NOT NULL, PRIMARY KEY(group_id,device_id))",
    "CREATE TABLE IF NOT EXISTS device_management ("
    "device_id TEXT PRIMARY KEY, realm_id TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]', "
    "revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS maintenance_jobs ("
    "id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, actor_id TEXT NOT NULL, "
    "realm_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, progress REAL, error TEXT, receipt_id TEXT, "
    "UNIQUE(realm_id,kind,idempotency_key))",
    "CREATE TABLE IF NOT EXISTS maintenance_receipts ("
    "id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, state TEXT NOT NULL, "
    "payload TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS backup_sets ("
    "id TEXT PRIMARY KEY, state TEXT NOT NULL, manifest_path TEXT NOT NULL, sha256 TEXT NOT NULL, "
    "schema_version INTEGER NOT NULL, instance_id TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS update_jobs ("
    "id TEXT PRIMARY KEY, release_id TEXT NOT NULL, state TEXT NOT NULL, receipt_path TEXT, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gateway_bootstrap ("
    "digest TEXT PRIMARY KEY, caller_sid TEXT NOT NULL, expires REAL NOT NULL, "
    "used INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS oidc_login_state ("
    "state_digest TEXT PRIMARY KEY, nonce TEXT NOT NULL, verifier TEXT NOT NULL, "
    "redirect_uri TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS diagnostic_logs ("
    "id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, level TEXT NOT NULL, event TEXT NOT NULL, "
    "message TEXT NOT NULL, device_id TEXT NOT NULL DEFAULT '', "
    "request_id TEXT NOT NULL DEFAULT '', "
    "actor_id TEXT NOT NULL DEFAULT '', fields_json TEXT NOT NULL DEFAULT '{}')",
    "INSERT OR IGNORE INTO gateway_settings(id,revision,settings_json,updated_at) "
    "VALUES (1,1,'{}','')",
    "CREATE INDEX IF NOT EXISTS management_users_realm ON management_users(realm_id,active)",
    "CREATE INDEX IF NOT EXISTS device_group_members_device ON device_group_members(device_id)",
    "CREATE INDEX IF NOT EXISTS device_management_realm ON device_management(realm_id,device_id)",
    "CREATE INDEX IF NOT EXISTS maintenance_jobs_realm ON maintenance_jobs(realm_id,created_at)",
    "CREATE INDEX IF NOT EXISTS diagnostic_logs_time ON diagnostic_logs(timestamp,id)",
    "CREATE INDEX IF NOT EXISTS diagnostic_logs_device ON diagnostic_logs(device_id,timestamp)",
)

MIGRATION_2 = (
    "CREATE INDEX IF NOT EXISTS audit_time ON audit(timestamp DESC,id DESC)",
    "CREATE INDEX IF NOT EXISTS audit_device_time ON audit(device_id,timestamp DESC,id DESC)",
    "CREATE INDEX IF NOT EXISTS audit_request_time ON audit(request_id,timestamp DESC,id DESC)",
    "CREATE INDEX IF NOT EXISTS audit_owner_time ON audit(owner_id,timestamp DESC,id DESC)",
    "CREATE INDEX IF NOT EXISTS diagnostic_logs_request_time "
    "ON diagnostic_logs(request_id,timestamp DESC,id DESC)",
    "CREATE INDEX IF NOT EXISTS diagnostic_logs_actor_time "
    "ON diagnostic_logs(actor_id,timestamp DESC,id DESC)",
)

MIGRATIONS: dict[int, tuple[str, ...]] = {1: MIGRATION_1, 2: MIGRATION_2}


@dataclass(frozen=True)
class MigrationReport:
    from_version: int
    to_version: int
    applied: tuple[int, ...]
    backups: tuple[str, ...]


def _checksum(statements: tuple[str, ...]) -> str:
    payload = "\n".join(statements).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _database_path(db: sqlite3.Connection) -> Path | None:
    row = db.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2] or row[2] == ":memory:":
        return None
    return Path(str(row[2])).resolve()


def _backup(db: sqlite3.Connection, version: int) -> str | None:
    source = _database_path(db)
    if source is None:
        return None
    destination = source.with_name(source.name + f".pre-gateway-v{version}.bak")
    target = sqlite3.connect(destination)
    try:
        db.backup(target)
    finally:
        target.close()
    return str(destination)


def migrate_gateway(
    db: sqlite3.Connection, target: int = CURRENT_GATEWAY_SCHEMA
) -> MigrationReport:
    if target < 0 or target > CURRENT_GATEWAY_SCHEMA:
        raise RuntimeError("unsupported Gateway schema target")
    db.execute(
        "CREATE TABLE IF NOT EXISTS gateway_schema ("
        "version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    rows = db.execute("SELECT version,checksum FROM gateway_schema ORDER BY version").fetchall()
    current = max((int(row[0]) for row in rows), default=0)
    if current > CURRENT_GATEWAY_SCHEMA or current > target:
        raise RuntimeError("unsupported future Gateway schema version")
    for row in rows:
        version = int(row[0])
        statements = MIGRATIONS.get(version)
        if statements is None or str(row[1]) != _checksum(statements):
            raise RuntimeError("Gateway migration checksum mismatch")
    applied: list[int] = []
    backups: list[str] = []
    for version in range(current + 1, target + 1):
        statements = MIGRATIONS[version]
        backup = _backup(db, version)
        if backup:
            backups.append(backup)
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                if statement.endswith("VALUES (1,1,'{}','')"):
                    db.execute(statement.replace("''", "?"), (timestamp(),))
                else:
                    db.execute(statement)
            db.execute(
                "INSERT INTO gateway_schema(version,checksum,applied_at) VALUES (?,?,?)",
                (version, _checksum(statements), timestamp()),
            )
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        applied.append(version)
    return MigrationReport(current, target, tuple(applied), tuple(backups))
