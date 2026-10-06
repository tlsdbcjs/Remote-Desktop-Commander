"""Transient owned-down state only; foreign keys and mouse coordinates are never retained."""

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Release:
    kind: str
    code: int
    scan: int
    flags: int
    since: float


class HeldInputs:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, int, int, int], Release] = {}
        self.failure = False

    def update(self, kind: str, code: int, scan: int, flags: int, up: bool) -> None:
        key = kind, code, scan, flags
        if up:
            self.pending.pop(key, None)
        elif key not in self.pending:
            if len(self.pending) >= 16:
                self.failure = True
                return
            self.pending[key] = Release(kind, code, scan, flags, time.monotonic())

    def keyboard(self, vk: int, scan: int, flags: int) -> None:
        if vk == 0xE7:  # VK_PACKET: preserve Unicode scan unit for its matching key-up.
            code, unit, release_flags = 0, scan, 6
        else:
            code, unit, release_flags = vk, 0, 2 | (flags & 1)
        self.update("key", code, unit, release_flags, bool(flags & 0x80))

    def mouse(self, message: int, data: int) -> None:
        pairs = {
            0x201: (4, False),
            0x202: (4, True),
            0x204: (16, False),
            0x205: (16, True),
            0x207: (64, False),
            0x208: (64, True),
        }
        if message in pairs:
            release, up = pairs[message]
            self.update("mouse", 0, 0, release, up)

    def snapshot(self) -> tuple[Release, ...]:
        return tuple(self.pending.copy().values())

    def overdue(self, seconds: float) -> bool:
        now = time.monotonic()
        return self.failure or any(now - release.since >= seconds for release in self.snapshot())
