import base64
import binascii
from typing import Literal

from pydantic import Field, model_validator

from racp_protocol.models import Identifier, StrictModel


class TerminalOpen(StrictModel):
    argv: list[str] | None = Field(default=None, min_length=1, max_length=256)
    shell: Literal["powershell", "cmd", "bash"] | None = None
    cwd: str | None = Field(default=None, max_length=4096)
    env: dict[str, str | None] = Field(default_factory=dict, max_length=256)
    cols: int = Field(default=120, ge=10, le=500)
    rows: int = Field(default=40, ge=5, le=300)

    @model_validator(mode="after")
    def bounded(self) -> "TerminalOpen":
        if self.argv is not None and self.shell is not None:
            raise ValueError("specify argv or shell")
        values = [*(self.argv or []), self.cwd or "", *self.env.keys()]
        values += [value for value in self.env.values() if value is not None]
        if any("\0" in value or len(value) > 32768 for value in values):
            raise ValueError("invalid terminal argument/environment")
        if self.argv is not None and not self.argv[0]:
            raise ValueError("terminal executable cannot be empty")
        if any(not key or "=" in key or len(key) > 256 for key in self.env):
            raise ValueError("invalid environment key")
        return self


class TerminalTarget(StrictModel):
    handle_id: Identifier
    agent_boot_id: Identifier | None = None


class TerminalRead(TerminalTarget):
    cursor: str = Field(default="0", pattern=r"^\d{1,20}$")
    max_bytes: int = Field(default=65536, ge=1, le=65536)
    wait_ms: int = Field(default=0, ge=0, le=20000)


class TerminalWrite(TerminalTarget):
    data: str = Field(max_length=87384)
    encoding: Literal["utf-8", "base64"] = "utf-8"

    def bytes(self) -> bytes:
        return (
            self.data.encode("utf-8")
            if self.encoding == "utf-8"
            else base64.b64decode(self.data, validate=True)
        )

    @model_validator(mode="after")
    def bounded(self) -> "TerminalWrite":
        try:
            if len(self.bytes()) > 65536:
                raise ValueError("terminal input exceeds 64 KiB")
        except binascii.Error as exc:
            raise ValueError("invalid raw base64 input") from exc
        return self


class TerminalResize(TerminalTarget):
    cols: int = Field(ge=10, le=500)
    rows: int = Field(ge=5, le=300)
