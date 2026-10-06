import asyncio
import re
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.console import ConsoleEvent
from racp_protocol.models import new_id, timestamp
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from racp_gateway.store import GatewayStore


class EventResponse(StreamingResponse):
    def __init__(self, stream: AsyncGenerator[str, None], release: Callable[[], None]) -> None:
        super().__init__(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "close"},
        )
        self.stream, self.release = stream, release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.stream.aclose()
            self.release()


class EventFeed:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store
        self.changed = asyncio.Event()
        self.active: dict[str, int] = {}
        self.max_events = 10000
        self.replay_seconds = 600
        store.db.executescript("""
          CREATE TABLE IF NOT EXISTS console_event_meta (id TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS console_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, resource TEXT NOT NULL, device_id TEXT, operation_id TEXT);
          CREATE INDEX IF NOT EXISTS console_event_owner ON console_events(owner_id,sequence);
        """)
        row = store.db.execute("SELECT id FROM console_event_meta").fetchone()
        if row is None:
            self.id = new_id("events")
            store.db.execute("INSERT INTO console_event_meta VALUES (?)", (self.id,))
        else:
            self.id = row[0]

    def publish(self, owner: str, resource: str, request: dict[str, Any]) -> None:
        self.store.db.execute(
            "INSERT INTO console_events(owner_id,observed_at,resource,device_id,operation_id) "
            "VALUES (?,?,?,?,?)",
            (
                owner,
                timestamp(),
                resource,
                request.get("device_id") or None,
                request.get("operation_id") or None,
            ),
        )
        self.collect()
        self.changed.set()

    def collect(self) -> None:
        cutoff = (
            (datetime.now(UTC) - timedelta(seconds=self.replay_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.store.db.execute(
            "DELETE FROM console_events WHERE observed_at<=? OR sequence<="
            "(SELECT COALESCE(MAX(sequence),0)-? FROM console_events)",
            (cutoff, self.max_events),
        )

    def latest(self) -> int:
        row = self.store.db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='console_events'"
        ).fetchone()
        return int(row[0]) if row else 0

    def cursor(self, value: str | None) -> tuple[int, bool]:
        self.collect()
        latest = self.latest()
        if value is None:
            return latest, False
        if len(value) > 128 or not re.fullmatch(r"events_[0-9a-f]{32}:\d{1,20}", value):
            raise RACPError("INVALID_ARGUMENT", "invalid event cursor")
        stream, sequence = value.split(":")
        first = self.store.db.execute("SELECT MIN(sequence) FROM console_events").fetchone()[0]
        minimum = int(first) if first is not None else latest + 1
        position = int(sequence)
        return (
            (latest, True)
            if stream != self.id or position < minimum - 1 or position > latest
            else (position, False)
        )

    def frame(self, event: ConsoleEvent) -> str:
        return f"id: {event.event_id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"

    def stream(
        self, owner: str, cursor: str | None, authenticate: Callable[[], None]
    ) -> tuple[AsyncGenerator[str, None], Callable[[], None]]:
        position, refresh = self.cursor(cursor)
        if sum(self.active.values()) >= 32 or self.active.get(owner, 0) >= 16:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "Console event stream limit reached", retry_after_ms=1000
            )
        self.active[owner] = self.active.get(owner, 0) + 1
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self.active[owner] -= 1
            if not self.active[owner]:
                del self.active[owner]

        async def iterate() -> AsyncGenerator[str, None]:
            nonlocal position
            try:
                yield self.frame(
                    ConsoleEvent(
                        event_id=f"{self.id}:{position}",
                        type="refresh" if refresh else "ready",
                        observed_at=timestamp(),
                        resource="snapshot",
                        full_refresh=refresh,
                    )
                )
                while True:
                    try:
                        authenticate()
                    except RACPError:
                        yield self.frame(
                            ConsoleEvent(
                                event_id=f"{self.id}:{position}",
                                type="session_expired",
                                observed_at=timestamp(),
                                resource="session",
                            )
                        )
                        return
                    _, gap = self.cursor(f"{self.id}:{position}")
                    if gap:
                        position = self.latest()
                        yield self.frame(
                            ConsoleEvent(
                                event_id=f"{self.id}:{position}",
                                type="refresh",
                                observed_at=timestamp(),
                                resource="snapshot",
                                full_refresh=True,
                            )
                        )
                    self.changed.clear()
                    rows = self.store.db.execute(
                        "SELECT * FROM console_events WHERE owner_id=? AND sequence>? "
                        "ORDER BY sequence LIMIT 100",
                        (owner, position),
                    ).fetchall()
                    if rows:
                        for row in rows:
                            position = row["sequence"]
                            yield self.frame(
                                ConsoleEvent(
                                    event_id=f"{self.id}:{position}",
                                    type="refresh"
                                    if row["resource"] == "browser_gap"
                                    else "change",
                                    observed_at=row["observed_at"],
                                    resource=row["resource"],
                                    device_id=row["device_id"],
                                    operation_id=row["operation_id"],
                                    full_refresh=row["resource"] == "browser_gap",
                                )
                            )
                        continue
                    try:
                        await asyncio.wait_for(self.changed.wait(), 10)
                    except TimeoutError:
                        yield self.frame(
                            ConsoleEvent(
                                event_id=f"{self.id}:{position}",
                                type="ready",
                                observed_at=timestamp(),
                                resource="keepalive",
                            )
                        )
            finally:
                release()

        return iterate(), release
