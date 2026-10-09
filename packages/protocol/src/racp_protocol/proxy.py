"""Process-bound native proxy carrier inputs; no global OS proxy changes."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from racp_protocol.native_operations import NativeTarget
from racp_protocol.provider_models import ProcessTarget


class ProxyPrepare(ProcessTarget):
    direction: Literal["agent_listener", "agent_connector"]
    local_port: Annotated[int, Field(ge=1, le=65535)] | None = None
    max_bytes: Annotated[int, Field(ge=16384, le=64 * 1024**2)] = 8 * 1024**2
    lease_seconds: Annotated[int, Field(ge=10, le=3600)] = 120

    @model_validator(mode="after")
    def endpoint(self) -> "ProxyPrepare":
        if (self.direction == "agent_connector") != (self.local_port is not None):
            raise ValueError("connector requires fixed local port; listener port is Agent-selected")
        return self


PROXY_MODELS: dict[str, type[BaseModel]] = {
    "proxy.prepare": ProxyPrepare,
    "proxy.close": NativeTarget,
}
