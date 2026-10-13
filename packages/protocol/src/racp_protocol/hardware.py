"""Snapshot metadata only; no raw device selectors or hardware IO requests."""
from pydantic import BaseModel, Field

from racp_protocol.models import Identifier, StrictModel


class HardwareTarget(StrictModel):
    agent_device_id: Identifier
    agent_boot_id: Identifier
    hardware_id: str = Field(pattern=r"^hardware_[0-9a-f]{32}$")
    interface_id: str = Field(pattern=r"^hwif_[0-9a-f]{32}$")
    connection_generation: str = Field(pattern=r"^connection_[0-9a-f]{32}$")


class HardwareEnumerate(StrictModel):
    limit: int = Field(default=100, ge=1, le=256)


class HardwareInspect(StrictModel):
    target: HardwareTarget


HARDWARE_MODELS: dict[str, type[BaseModel]] = {
    "hardware.enumerate": HardwareEnumerate,
    "hardware.inspect": HardwareInspect,
}
