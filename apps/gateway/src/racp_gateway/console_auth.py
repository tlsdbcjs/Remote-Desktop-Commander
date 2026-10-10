import secrets
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.console import ConsoleSession
from racp_sdk.security import digest, token

from racp_gateway.store import GatewayStore

COOKIE = "racp_console"


class ConsoleAuth:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store
        store.db.executescript("""
          CREATE TABLE IF NOT EXISTS console_setup (
            digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL, clock_id TEXT NOT NULL,
            deadline REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS console_sessions (
            digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL, clock_id TEXT NOT NULL,
            created_monotonic REAL NOT NULL, last_activity REAL NOT NULL, expires_at TEXT NOT NULL);
        """)

    def setup(self, owner: str) -> dict[str, Any]:
        secret = token()
        with self.store.transaction():
            active = self.store.db.execute(
                "SELECT COUNT(*) FROM console_setup WHERE used=0 AND clock_id=? AND deadline>?",
                (self.store.retention_clock_id, monotonic()),
            ).fetchone()[0]
            if active >= 8:
                raise RACPError("RESOURCE_EXHAUSTED", "Console setup token limit reached")
            self.store.db.execute(
                "INSERT INTO console_setup VALUES (?,?,?,?,0)",
                (digest(secret), owner, self.store.retention_clock_id, monotonic() + 300),
            )
            self.store.audit("console_setup_created", {"context": {"principal_id": owner}})
        return {"setup_secret": secret, "expires_in_seconds": 300}

    def exchange(self, secret: str) -> tuple[str, ConsoleSession]:
        credential = token()
        current = monotonic()
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT * FROM console_setup WHERE digest=?", (digest(secret),)
            ).fetchone()
            if (
                row is None
                or row["used"]
                or row["clock_id"] != self.store.retention_clock_id
                or row["deadline"] <= current
            ):
                raise RACPError(
                    "UNAUTHENTICATED", "invalid, expired or consumed Console setup token"
                )
            active = self.store.db.execute(
                "SELECT COUNT(*) FROM console_sessions WHERE clock_id=? "
                "AND last_activity>? AND created_monotonic>?",
                (self.store.retention_clock_id, current - 1800, current - 43200),
            ).fetchone()[0]
            if active >= 16:
                raise RACPError("RESOURCE_EXHAUSTED", "Console session limit reached")
            self.store.db.execute(
                "UPDATE console_setup SET used=1 WHERE digest=?", (digest(secret),)
            )
            expires = (datetime.now(UTC) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
            self.store.db.execute(
                "INSERT INTO console_sessions VALUES (?,?,?,?,?,?)",
                (
                    digest(credential),
                    row["owner_id"],
                    self.store.retention_clock_id,
                    current,
                    current,
                    expires,
                ),
            )
            self.store.audit(
                "console_session_created", {"context": {"principal_id": row["owner_id"]}}
            )
        return credential, self.view(credential)

    def create_session(self, owner: str) -> tuple[str, ConsoleSession]:
        """Create a browser session for an already authenticated local/OIDC principal."""
        credential = token()
        current = monotonic()
        with self.store.transaction():
            active = self.store.db.execute(
                "SELECT COUNT(*) FROM console_sessions WHERE clock_id=? "
                "AND last_activity>? AND created_monotonic>?",
                (self.store.retention_clock_id, current - 1800, current - 43200),
            ).fetchone()[0]
            if active >= 16:
                raise RACPError("RESOURCE_EXHAUSTED", "Console session limit reached")
            expires = (datetime.now(UTC) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
            self.store.db.execute(
                "INSERT INTO console_sessions VALUES (?,?,?,?,?,?)",
                (
                    digest(credential),
                    owner,
                    self.store.retention_clock_id,
                    current,
                    current,
                    expires,
                ),
            )
            self.store.audit("console_session_created", {"context": {"principal_id": owner}})
        return credential, self.view(credential)

    def authenticate(self, credential: str, *, csrf: str | None = None, touch: bool = False) -> str:
        row = self.store.db.execute(
            "SELECT * FROM console_sessions WHERE digest=?", (digest(credential),)
        ).fetchone()
        current = monotonic()
        if (
            row is None
            or row["clock_id"] != self.store.retention_clock_id
            or current < row["created_monotonic"]
            or current - row["last_activity"] >= 1800
            or current - row["created_monotonic"] >= 43200
        ):
            raise RACPError(
                "SESSION_EXPIRED", "Console session expired; exchange a new setup token"
            )
        if csrf is not None and not secrets.compare_digest(csrf, digest(credential + ":csrf")):
            raise RACPError("PERMISSION_DENIED", "Console CSRF token differs")
        if touch:
            self.store.db.execute(
                "UPDATE console_sessions SET last_activity=? WHERE digest=?",
                (current, digest(credential)),
            )
        return str(row["owner_id"])

    def view(self, credential: str) -> ConsoleSession:
        owner = self.authenticate(credential)
        row = self.store.db.execute(
            "SELECT expires_at FROM console_sessions WHERE digest=?", (digest(credential),)
        ).fetchone()
        return ConsoleSession(
            owner_id=owner, csrf_token=digest(credential + ":csrf"), expires_at=row["expires_at"]
        )

    def logout(self, credential: str) -> None:
        owner = self.authenticate(credential)
        with self.store.transaction():
            self.store.db.execute(
                "DELETE FROM console_sessions WHERE digest=?", (digest(credential),)
            )
            self.store.audit("console_session_revoked", {"context": {"principal_id": owner}})

    def collect(self) -> None:
        current = monotonic()
        self.store.db.execute(
            "DELETE FROM console_setup WHERE used=1 OR clock_id<>? OR deadline<=?",
            (self.store.retention_clock_id, current),
        )
        self.store.db.execute(
            "DELETE FROM console_sessions WHERE clock_id<>? OR last_activity<=? "
            "OR created_monotonic<=?",
            (self.store.retention_clock_id, current - 1800, current - 43200),
        )
