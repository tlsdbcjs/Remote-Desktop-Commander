"""Session-explicit desktop observations and mandatory exclusive input leases."""

from typing import Literal

from pydantic import Field, model_validator

from racp_protocol.models import Identifier, StrictModel


class DesktopSessions(StrictModel):
    pass


class DesktopSession(StrictModel):
    session_id: int = Field(ge=1, le=0xFFFFFFFF)


class DesktopWindows(DesktopSession):
    limit: int = Field(default=64, ge=1, le=128)


class DesktopInspect(DesktopSession):
    window_id: Identifier
    limit: int = Field(default=64, ge=1, le=64)


class DesktopScreenshot(DesktopSession):
    window_id: Identifier | None = None
    monitor_id: Identifier | None = None
    preview: bool = True

    @model_validator(mode="after")
    def one_scope(self) -> "DesktopScreenshot":
        if self.window_id and self.monitor_id:
            raise ValueError("select a window or a monitor, not both")
        return self


class DesktopLeaseAcquire(DesktopSession):
    ttl_ms: int = Field(default=15000, ge=1000, le=15000)


class DesktopLease(DesktopSession):
    lease_id: Identifier


class DesktopLeaseRenew(DesktopLease):
    ttl_ms: int = Field(default=15000, ge=1000, le=15000)


class DesktopInput(DesktopLease):
    observation_id: Identifier
    layout_revision: Identifier
    expected_window_id: Identifier


class DesktopPoint(DesktopInput):
    x: int = Field(ge=-65536, le=65536)
    y: int = Field(ge=-65536, le=65536)


class DesktopElement(DesktopInput):
    element_ref: Identifier


class DesktopValue(DesktopElement):
    value: str = Field(max_length=4096)


class DesktopClick(DesktopPoint):
    button: Literal["left", "right", "middle"] = "left"
    click_count: int = Field(default=1, ge=1, le=2)


class DesktopType(DesktopInput):
    text: str = Field(min_length=1, max_length=4096)


class DesktopKey(DesktopInput):
    keys: list[
        Literal[
            "CTRL",
            "ALT",
            "SHIFT",
            "ENTER",
            "TAB",
            "ESC",
            "BACKSPACE",
            "DELETE",
            "HOME",
            "END",
            "LEFT",
            "RIGHT",
            "UP",
            "DOWN",
            "SPACE",
            "A",
            "C",
            "V",
            "X",
            "Z",
        ]
    ] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def distinct_keys(self) -> "DesktopKey":
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("keys must be distinct")
        return self


class DesktopScroll(DesktopPoint):
    delta: int = Field(ge=-1200, le=1200)


class DesktopDrag(DesktopPoint):
    end_x: int = Field(ge=-65536, le=65536)
    end_y: int = Field(ge=-65536, le=65536)
    duration_ms: int = Field(default=300, ge=50, le=1000)


DESKTOP_MODELS: dict[str, type[StrictModel]] = {
    "desktop.sessions": DesktopSessions,
    "desktop.monitors": DesktopSession,
    "desktop.foreground": DesktopSession,
    "desktop.windows": DesktopWindows,
    "desktop.inspect": DesktopInspect,
    "desktop.screenshot": DesktopScreenshot,
    "desktop.lease_acquire": DesktopLeaseAcquire,
    "desktop.lease_renew": DesktopLeaseRenew,
    "desktop.lease_release": DesktopLease,
    "desktop.activate": DesktopInput,
    "desktop.move": DesktopPoint,
    "desktop.click": DesktopClick,
    "desktop.type": DesktopType,
    "desktop.key": DesktopKey,
    "desktop.scroll": DesktopScroll,
    "desktop.drag": DesktopDrag,
    "desktop.invoke": DesktopElement,
    "desktop.set_value": DesktopValue,
}
DESKTOP_READS = frozenset(
    {
        "desktop.sessions",
        "desktop.monitors",
        "desktop.foreground",
        "desktop.windows",
        "desktop.inspect",
        "desktop.screenshot",
    }
)
