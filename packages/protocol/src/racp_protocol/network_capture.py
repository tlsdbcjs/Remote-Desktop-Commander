"""An explicit IPv4 TCP/UDP flow, bounded time and size, with no caller output path."""

import ipaddress

from pydantic import Field, field_validator

from racp_protocol.models import StrictModel


class NetworkCapture(StrictModel):
    local_ip: str = Field(max_length=15)
    peer_ip: str = Field(max_length=15)
    local_port: int = Field(ge=1, le=65535)
    duration_ms: int = Field(default=3000, ge=100, le=30000)
    max_bytes: int = Field(default=16 * 1024**2, ge=68, le=16 * 1024**2)

    @field_validator("local_ip", "peer_ip")
    @classmethod
    def explicit_ipv4(cls, value: str) -> str:
        address = ipaddress.IPv4Address(value)
        if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
            raise ValueError("Capture requires explicit unicast IPv4 addresses")
        return str(address)
