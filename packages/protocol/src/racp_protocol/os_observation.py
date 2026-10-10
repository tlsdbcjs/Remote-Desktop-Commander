"""Bounded, typed OS observation inputs. No arbitrary command/query language."""

from pydantic import BaseModel, Field

from racp_protocol.models import StrictModel


class SystemObservation(StrictModel):
    pass


class EnvironmentQuery(StrictModel):
    keys: list[str] = Field(default_factory=list, max_length=64)


class ObservationList(StrictModel):
    limit: int = Field(default=100, ge=1, le=500)


class ConnectionsQuery(ObservationList):
    pid: int | None = Field(default=None, ge=1, le=0xFFFFFFFF)


class InventoryPage(StrictModel):
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10000)


class ServiceQuery(StrictModel):
    name: str = Field(min_length=1, max_length=256, pattern=r"^[^/\\\x00-\x1f]+$")


OS_OBSERVATION_MODELS: dict[str, type[BaseModel]] = {
    "system.info": SystemObservation,
    "system.resources": SystemObservation,
    "system.locale": SystemObservation,
    "system.environment": EnvironmentQuery,
    "network.interfaces": ObservationList,
    "network.connections": ConnectionsQuery,
    "storage.volumes": ObservationList,
    "services.list": InventoryPage,
    "services.get": ServiceQuery,
    "software.inventory": InventoryPage,
}
OS_OBSERVATION_READS = frozenset(OS_OBSERVATION_MODELS)
WINDOWS_INVENTORY_READS = frozenset({"services.list", "services.get", "software.inventory"})
