"""Native capture filters, PCAP integrity and bounded socket ownership."""

import ipaddress
import os
import struct
from pathlib import Path

import pytest
from racp_agent.packet_capture import CaptureFilter, CaptureUnavailable, capture_ipv4


def packet(source: str, destination: str, sport: int, dport: int, fragment: int = 0) -> bytes:
    tcp = struct.pack("!HHIIHHHH", sport, dport, 1, 0, 0x5010, 1024, 0, 0)
    return (
        struct.pack(
            "!BBHHHBBH4s4s",
            0x45,
            0,
            40,
            1,
            fragment,
            64,
            6,
            0,
            ipaddress.ip_address(source).packed,
            ipaddress.ip_address(destination).packed,
        )
        + tcp
    )


def test_filter_retains_both_directions_and_excludes_other_flows() -> None:
    scope = CaptureFilter("192.168.29.121", "192.168.29.141", 50123)
    assert scope.matches(packet(scope.local_ip, scope.peer_ip, 50123, 8765))
    assert scope.matches(packet(scope.peer_ip, scope.local_ip, 8765, 50123))
    assert not scope.matches(packet(scope.local_ip, scope.peer_ip, 50124, 8765))
    assert not scope.matches(packet("192.168.29.122", scope.peer_ip, 50123, 8765))
    assert not scope.matches(packet(scope.local_ip, scope.peer_ip, 50123, 8765, 1))
    assert not scope.matches(b"\x45" * 4)


class OwnedSocket:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = iter(frames)
        self.controls: list[tuple[int, int]] = []
        self.closed = False

    def bind(self, value: tuple[str, int]) -> None:
        pass

    def settimeout(self, value: float) -> None:
        pass

    def ioctl(self, code: int, value: int) -> None:
        self.controls.append((code, value))

    def recv(self, size: int) -> bytes:
        try:
            return next(self.frames)
        except StopIteration:
            raise TimeoutError from None

    def close(self) -> None:
        self.closed = True


@pytest.mark.skipif(os.name != "nt", reason="Windows raw socket ownership")
def test_pcap_contains_only_matching_complete_packets_and_releases_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import socket

    own = packet("192.168.29.121", "192.168.29.141", 50123, 8765)
    foreign = packet("192.168.29.121", "192.168.29.141", 50124, 8765)
    raw = OwnedSocket([foreign, own])
    monkeypatch.setattr(socket, "socket", lambda *args: raw)
    path = tmp_path / "own.pcap"
    receipt = capture_ipv4(
        CaptureFilter("192.168.29.121", "192.168.29.141", 50123), path, duration_seconds=0.01
    )
    data = path.read_bytes()
    assert struct.unpack("<IHHIIII", data[:24])[-1] == 101
    assert struct.unpack("<IIII", data[24:40])[2:] == (len(own), len(own))
    assert data[40:] == own
    assert receipt["packets"] == 1 and receipt["bytes"] == len(data)
    assert raw.controls[0] == (socket.SIO_RCVALL, 3)
    assert raw.controls[-1] == (socket.SIO_RCVALL, socket.RCVALL_OFF) and raw.closed


@pytest.mark.skipif(os.name != "nt", reason="Windows empty capture handling")
def test_empty_capture_is_an_error_and_still_releases_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import socket

    raw = OwnedSocket([])
    monkeypatch.setattr(socket, "socket", lambda *args: raw)
    with pytest.raises(CaptureUnavailable, match="no_matching_packets"):
        capture_ipv4(
            CaptureFilter("192.168.29.121", "192.168.29.141", 50123),
            tmp_path / "empty.pcap",
            duration_seconds=0.01,
        )
    assert raw.controls[-1] == (socket.SIO_RCVALL, socket.RCVALL_OFF) and raw.closed


@pytest.mark.skipif(os.name != "nt", reason="Windows capture byte budget")
def test_byte_budget_keeps_whole_packets_and_reports_partial_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import socket

    own = packet("192.168.29.121", "192.168.29.141", 50123, 8765)
    raw = OwnedSocket([own, own])
    monkeypatch.setattr(socket, "socket", lambda *args: raw)
    path = tmp_path / "bounded.pcap"
    receipt = capture_ipv4(
        CaptureFilter("192.168.29.121", "192.168.29.141", 50123),
        path,
        duration_seconds=0.01,
        max_bytes=80,
    )
    assert path.stat().st_size == 80 and receipt["packets"] == 1
    assert receipt["complete"] is False and receipt["termination_reason"] == "byte_limit"
    assert raw.closed
