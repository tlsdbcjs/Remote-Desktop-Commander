"""Bounded metadata outbox: ACKed sequence replay, with explicit overflow refresh."""

from collections import deque
from typing import Any

from racp_domain.models import RACPError


class BrowserOutbox:
    def __init__(self, limit: int = 128) -> None:
        self.limit, self.sequence, self.sent, self.acked = limit, 0, 0, 0
        self.pending: deque[dict[str, Any]] = deque()
        self.capability: dict[str, Any] | None = None

    def push(self, event: dict[str, Any], inventory: list[dict[str, Any]]) -> None:
        self.sequence += 1
        if event.get("capability") is not None:
            self.capability = event["capability"]
        if len(self.pending) >= self.limit:
            self.pending.popleft()
            event = {"kind": "gap", "state": "refresh_required", "handles": inventory}
            if self.capability is not None:
                event["capability"] = self.capability
        self.pending.append({**event, "event_sequence": str(self.sequence)})

    def ack(self, sequence: str) -> None:
        value = int(sequence)
        if value > self.sent:
            raise RACPError("INVALID_ARGUMENT", "browser ACK exceeds sent sequence", layer="agent")
        self.acked = max(self.acked, value)
        while self.pending and int(self.pending[0]["event_sequence"]) <= self.acked:
            self.pending.popleft()
