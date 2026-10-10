"""An Agent-owned, contained capture recipe. No caller command or output path."""

import ctypes
import hashlib
import os
import struct
import sys
from pathlib import Path
from typing import Any

from racp_agent import packet_capture
from racp_agent.packet_capture import CaptureFilter
from racp_agent.plugins.process import ContainedCommand
from racp_agent.providers.owned_recipe import OwnedArtifactRecipe
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import Capability
from racp_protocol.network_capture import NetworkCapture


class NetworkCaptureProvider(OwnedArtifactRecipe):
    suffix = ".pcap"
    media_type = "application/vnd.tcpdump.pcap"

    def validate(self, payload: dict[str, Any], context: ExecutionContext) -> NetworkCapture:
        return NetworkCapture.model_validate(payload)

    def capability(self) -> Capability:
        supported = os.name == "nt"
        administrator = supported and bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
        return Capability(
            name="network_capture",
            version="1.0.0",
            operations=["network.capture"],
            supported=supported,
            enabled=administrator,
            healthy=administrator,
            unavailable_reason=None
            if administrator
            else "needs_administrator"
            if supported
            else "unsupported_os",
            attributes={
                "backend": "Windows Winsock SIO_RCVALL",
                "link_type": "raw_ipv4",
                "max_duration_ms": 30000,
                "max_bytes": 16777216,
                "scope": "selected_ipv4_tcp_udp_flow",
                "tls_decryption": False,
                "containment": "windows-job-object",
                "manual_analysis_tool_required": False,
            },
        )

    def command(self, data: NetworkCapture, path: Path) -> ContainedCommand:
        return ContainedCommand(
            command=[
                sys.executable,
                "-I",
                str(Path(packet_capture.__file__).resolve()),
                "--local-ip",
                data.local_ip,
                "--peer-ip",
                data.peer_ip,
                "--local-port",
                str(data.local_port),
                "--output",
                str(path),
                "--duration",
                str(data.duration_ms / 1000),
                "--max-bytes",
                str(data.max_bytes),
            ],
            working_directory=str(self.spool),
        )

    def verify(self, path: Path, receipt: dict[str, Any], data: NetworkCapture) -> None:
        with self.guard.parent(path) as parent:
            with os.fdopen(parent.open(path.name, os.O_RDONLY), "rb") as stream:
                content = stream.read(data.max_bytes + 1)
        if len(content) > data.max_bytes:
            raise RACPError("RESOURCE_EXHAUSTED", "Capture exceeded byte budget", layer="provider")
        if (
            len(content) < 68
            or receipt.get("bytes") != len(content)
            or receipt.get("sha256") != hashlib.sha256(content).hexdigest()
            or content[:24] != struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101)
        ):
            raise RACPError("CAPABILITY_UNAVAILABLE", "Invalid capture receipt", layer="provider")
        scope = CaptureFilter(data.local_ip, data.peer_ip, data.local_port)
        position, count = 24, 0
        while position < len(content):
            if position + 16 > len(content):
                raise RACPError("CAPABILITY_UNAVAILABLE", "Partial PCAP record", layer="provider")
            _, usec, captured, original = struct.unpack("<IIII", content[position : position + 16])
            position += 16
            packet = content[position : position + captured]
            if (
                usec >= 1000000
                or captured != original
                or captured > 65535
                or len(packet) != captured
                or not scope.matches(packet)
            ):
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE", "Capture escaped its flow", layer="provider"
                )
            position += captured
            count += 1
        if (
            receipt.get("packets") != count
            or not count
            or receipt.get("local_ip") != data.local_ip
            or receipt.get("peer_ip") != data.peer_ip
            or receipt.get("local_port") != data.local_port
            or receipt.get("raw_socket_closed") is not True
            or receipt.get("link_type") != "raw_ipv4"
            or receipt.get("tls_decryption") is not False
        ):
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "Capture scope receipt mismatch", layer="provider"
            )

