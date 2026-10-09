"""Exact loopback socket peer identity for process-bound native channels."""

import asyncio

import psutil
from racp_domain.models import RACPError


def verify_tcp_peer(writer: asyncio.StreamWriter, pid: int, created: float) -> None:
    local, peer = writer.get_extra_info("sockname"), writer.get_extra_info("peername")
    if not local or not peer or local[0] != "127.0.0.1" or peer[0] != "127.0.0.1":
        raise RACPError("PERMISSION_DENIED", "Native peer must be loopback", layer="agent")
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - created) > 1e-6:
            raise RACPError("PRECONDITION_FAILED", "Native peer PID changed", layer="agent")
        owners = {
            row.pid
            for row in psutil.net_connections(kind="tcp")
            if row.laddr
            and row.raddr
            and tuple(row.laddr) == tuple(peer)
            and tuple(row.raddr) == tuple(local)
            and row.status == psutil.CONN_ESTABLISHED
        }
        if owners != {pid}:
            raise RACPError("PERMISSION_DENIED", "Native socket process differs", layer="agent")
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as error:
        raise RACPError(
            "PERMISSION_DENIED", "Native peer identity unavailable", layer="agent"
        ) from error
