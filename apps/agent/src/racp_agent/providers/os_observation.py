"""OS account observations with explicit provenance; never delegate to a shell."""

import asyncio
import ctypes
import locale
import os
import platform
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from racp_agent.execution_identity import execution_identity
from racp_agent.providers.windows_inventory import service_get, services_list, software_list
from racp_domain.models import ExecutionContext, RACPError
from racp_domain.version import VERSION
from racp_protocol.os_observation import OS_OBSERVATION_MODELS
from racp_protocol.registry import validate_payload

SAFE_ENVIRONMENT = frozenset(
    {
        "systemroot",
        "windir",
        "systemdrive",
        "programdata",
        "allusersprofile",
        "programfiles",
        "programfiles(x86)",
        "userprofile",
        "home",
        "lang",
        "lc_all",
        "tz",
    }
)


def system_info() -> dict[str, Any]:
    if os.name == "nt":
        # Native identity avoids platform.uname's optional WMI query. OS and
        # architecture must not come from spoofable environment variables.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetNativeSystemInfo.argtypes = [ctypes.c_void_p]
        kernel.GetNativeSystemInfo.restype = None
        info = ctypes.create_string_buffer(64)
        kernel.GetNativeSystemInfo(info)
        architecture = {0: "x86", 9: "AMD64", 12: "ARM64"}.get(
            int.from_bytes(info.raw[:2], "little"), "unknown"
        )
        version = sys.getwindowsversion()
        release = f"{version.major}.{version.minor}.{version.build}"
    else:
        architecture, release = platform.machine(), platform.release()
    return {
        "hostname": socket.gethostname(),
        "platform": "Windows" if os.name == "nt" else platform.system(),
        "architecture": architecture,
        "os_version": release,
        "agent_version": VERSION,
        "execution_identity": execution_identity(),
        "scope": "os_account",
    }


def endpoint(value: Any) -> dict[str, Any] | None:
    return {"ip": value.ip, "port": value.port} if value else None


class OSObservationProvider:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def observe(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == "services.list":
            return services_list(payload)
        if operation == "services.get":
            return service_get(payload)
        if operation == "software.inventory":
            return software_list(payload)
        if operation == "system.info":
            return system_info()
        if operation == "system.resources":
            memory = psutil.virtual_memory()
            return {
                "cpu_count": psutil.cpu_count(),
                "cpu_times_seconds": psutil.cpu_times()._asdict(),
                "memory": {
                    "total_bytes": memory.total,
                    "available_bytes": memory.available,
                    "used_bytes": memory.used,
                    "percent": memory.percent,
                },
                "uptime_seconds": max(0, time.time() - psutil.boot_time()),
                "cpu_measurement": "cumulative_cpu_times",
            }
        if operation == "system.locale":
            local = datetime.now().astimezone()
            offset = local.utcoffset()
            return {
                "timezone": local.tzname(),
                "utc_offset_seconds": offset.total_seconds() if offset else 0,
                "process_locale": locale.getlocale(),
                "filesystem_encoding": sys.getfilesystemencoding(),
                "preferred_encoding": locale.getencoding(),
            }
        if operation == "system.environment":
            if any(
                key.casefold() not in SAFE_ENVIRONMENT or len(key) > 256 for key in payload["keys"]
            ):
                raise RACPError(
                    "PERMISSION_DENIED",
                    "Environment key is outside the safe observation set",
                    layer="provider",
                )
            return {
                "values": {key: os.environ.get(key) for key in payload["keys"]},
                "scope": "safe_process_environment_keys",
            }
        if operation == "network.interfaces":
            stats, counters = psutil.net_if_stats(), psutil.net_io_counters(pernic=True)
            items = []
            for name, addresses in sorted(psutil.net_if_addrs().items()):
                info, io = stats.get(name), counters.get(name)
                items.append(
                    {
                        "name": name,
                        "addresses": [
                            {
                                "family": int(item.family),
                                "address": item.address,
                                "netmask": item.netmask,
                                "broadcast": item.broadcast,
                            }
                            for item in addresses
                        ],
                        "is_up": info.isup if info else None,
                        "mtu": info.mtu if info else None,
                        "speed_mbps": info.speed if info else None,
                        "counters": io._asdict() if io else None,
                    }
                )
            return {
                "items": items[: payload["limit"]],
                "truncated": len(items) > payload["limit"],
                "consistency": "live_observation",
                "route_query_supported": False,
                "dns_server_query_supported": False,
            }
        if operation == "network.connections":
            pid = payload["pid"]
            connections = (
                psutil.Process(pid).net_connections("inet")
                if pid
                else psutil.net_connections("inet")
            )
            items = [
                {
                    "pid": pid if pid else getattr(item, "pid", None),
                    "family": int(item.family),
                    "type": int(item.type),
                    "local": endpoint(item.laddr),
                    "remote": endpoint(item.raddr),
                    "status": item.status,
                }
                for item in connections
            ]
            items.sort(key=lambda item: (item["pid"] or 0, str(item["local"]), str(item["remote"])))
            return {
                "items": items[: payload["limit"]],
                "truncated": len(items) > payload["limit"],
                "consistency": "live_observation",
                "visibility": "current_os_account",
            }
        if operation == "storage.volumes":
            partitions = psutil.disk_partitions(all=False)
            items = []
            for partition in partitions[: payload["limit"]]:
                item: dict[str, Any] = {
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "filesystem": partition.fstype,
                    "options": partition.opts,
                }
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    item.update(
                        total_bytes=usage.total,
                        free_bytes=usage.free,
                        used_bytes=usage.used,
                        availability="available",
                    )
                except OSError:
                    item["availability"] = "unavailable"
                items.append(item)
            return {
                "items": items,
                "truncated": len(partitions) > payload["limit"],
                "consistency": "live_observation",
            }
        raise RACPError(
            "CAPABILITY_UNAVAILABLE",
            "OS observation operation is not implemented",
            layer="provider",
        )

    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        if operation not in OS_OBSERVATION_MODELS:
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "OS observation operation is not implemented",
                layer="provider",
            )
        payload = validate_payload(operation, payload)
        try:
            async with asyncio.timeout(context.timeout_ms / 1000):
                result = await asyncio.to_thread(self.observe, operation, payload)
        except TimeoutError:
            raise RACPError(
                "TIMEOUT", "OS observation exceeded its request budget", layer="provider"
            ) from None
        except psutil.AccessDenied:
            raise RACPError(
                "PERMISSION_DENIED", "OS denied observation access", layer="provider"
            ) from None
        except psutil.NoSuchProcess:
            raise RACPError(
                "PROCESS_NOT_FOUND", "Observed process no longer exists", layer="provider"
            ) from None
        return {
            "state": "SUCCEEDED",
            "error": None,
            "result": {
                **result,
                "device_id": context.device_id,
                "agent_boot_id": context.agent_boot_id,
                "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
        }
