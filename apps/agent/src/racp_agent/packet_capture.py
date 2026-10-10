"""Bounded Windows IPv4 capture for analysis on the controller's existing tools.

Run through the existing approved shell operation. This uses the Windows Winsock
API, requires an administrator token, and installs no driver or analysis tool.
PCAP link type 101 contains IP packets, not Ethernet/ARP or plaintext TLS secrets.
"""

import argparse
import hashlib
import ipaddress
import json
import math
import os
import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CaptureUnavailable(RuntimeError):
    """Capture produced no usable evidence or this platform is unsupported."""


@dataclass(frozen=True)
class CaptureFilter:
    local_ip: str
    peer_ip: str
    local_port: int

    def __post_init__(self) -> None:
        for value in (self.local_ip, self.peer_ip):
            address = ipaddress.IPv4Address(value)
            if address.is_unspecified or address.is_multicast:
                raise ValueError("capture requires explicit unicast IPv4 addresses")
        if isinstance(self.local_port, bool) or not 1 <= self.local_port <= 65535:
            raise ValueError("capture requires a local TCP/UDP port")

    def matches(self, packet: bytes) -> bool:
        if len(packet) < 24 or packet[0] >> 4 != 4 or packet[9] not in {6, 17}:
            return False
        header = (packet[0] & 15) * 4
        length = int.from_bytes(packet[2:4], "big")
        minimum = 20 if packet[9] == 6 else 8
        if header < 20 or length > len(packet) or length < header + minimum:
            return False
        # Non-initial fragments do not contain ports; never include unrelated
        # fragments merely because their source and destination match.
        if int.from_bytes(packet[6:8], "big") & 0x1FFF:
            return False
        source = socket.inet_ntoa(packet[12:16])
        destination = socket.inet_ntoa(packet[16:20])
        sport, dport = struct.unpack("!HH", packet[header : header + 4])
        return (
            source == self.local_ip and destination == self.peer_ip and sport == self.local_port
        ) or (source == self.peer_ip and destination == self.local_ip and dport == self.local_port)


def capture_ipv4(
    scope: CaptureFilter,
    output: Path,
    *,
    duration_seconds: float = 3,
    max_bytes: int = 16 * 1024 * 1024,
) -> dict[str, Any]:
    if os.name != "nt":
        raise CaptureUnavailable("Windows Winsock capture is unavailable on this platform")
    if not math.isfinite(duration_seconds) or not 0 < duration_seconds <= 30:
        raise ValueError("capture duration must be positive and at most 30 seconds")
    if isinstance(max_bytes, bool) or not 24 <= max_bytes <= 16 * 1024 * 1024:
        raise ValueError("capture byte limit must be between 24 and 16777216")
    # Exclusive creation preserves an existing file. The caller chooses an
    # output within its approved workspace, just as for other shell commands.
    packets = 0
    size = 24
    reason = "duration"
    hasher = hashlib.sha256()
    started = time.monotonic()
    raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
    armed = False
    try:
        raw.bind((scope.local_ip, 0))
        raw.settimeout(min(0.1, duration_seconds))
        with output.open("xb") as stream:
            # Receive local interface IP traffic without enabling promiscuous
            # mode on the NIC or changing other capture sessions.
            # Winsock RCVALL_IPLEVEL is 3; some Python/type-stub versions do
            # not expose its symbolic name.
            raw.ioctl(socket.SIO_RCVALL, getattr(socket, "RCVALL_IPLEVEL", 3))
            armed = True
            header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101)
            stream.write(header)
            hasher.update(header)
            deadline = time.monotonic() + duration_seconds
            while time.monotonic() < deadline:
                try:
                    packet = raw.recv(65535)
                except TimeoutError:
                    continue
                if not scope.matches(packet):
                    continue
                packet = packet[: int.from_bytes(packet[2:4], "big")]
                if size + 16 + len(packet) > max_bytes:
                    reason = "byte_limit"
                    break
                now = time.time_ns()
                record = (
                    struct.pack(
                        "<IIII",
                        now // 1_000_000_000,
                        now % 1_000_000_000 // 1000,
                        len(packet),
                        len(packet),
                    )
                    + packet
                )
                stream.write(record)
                hasher.update(record)
                size += len(record)
                packets += 1
    finally:
        try:
            if armed:
                raw.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
        finally:
            raw.close()
    if not packets:
        raise CaptureUnavailable("no_matching_packets")
    return {
        "backend": "Windows Winsock SIO_RCVALL",
        "local_ip": scope.local_ip,
        "peer_ip": scope.peer_ip,
        "local_port": scope.local_port,
        "packets": packets,
        "bytes": size,
        "sha256": hasher.hexdigest(),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "complete": reason == "duration",
        "termination_reason": reason,
        "raw_socket_closed": True,
        "link_type": "raw_ipv4",
        "scope": "selected_ipv4_tcp_udp_flow",
        "non_initial_fragments": "excluded",
        "tls_decryption": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--peer-ip", required=True)
    parser.add_argument("--local-port", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    args = parser.parse_args()
    try:
        result = capture_ipv4(
            CaptureFilter(args.local_ip, args.peer_ip, args.local_port),
            args.output,
            duration_seconds=args.duration,
            max_bytes=args.max_bytes,
        )
    except (OSError, ValueError, CaptureUnavailable) as error:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_type": type(error).__name__,
                    "reason": str(error),
                    "errno": getattr(error, "errno", None),
                    "winerror": getattr(error, "winerror", None),
                }
            )
        )
        return 1
    print(json.dumps({"status": "SUCCEEDED", **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
