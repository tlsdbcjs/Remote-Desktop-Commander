"""Bounded Windows inventory without command lines, uninstall commands or secrets."""

import ctypes
import os
from itertools import islice
from typing import Any

import psutil
from racp_domain.models import RACPError


def require_windows() -> None:
    if os.name != "nt":
        raise RACPError("CAPABILITY_UNAVAILABLE", "Windows inventory required", layer="provider")


def service_record(service: Any) -> dict[str, Any]:
    # Do not call as_dict(): it includes binary arguments and the logon account.
    name = service.name()
    try:
        return {
            "name": name,
            "display_name": service.display_name(),
            "status": service.status(),
            "start_type": service.start_type(),
            "availability": "available",
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return {"name": name, "availability": "unavailable"}


def services_list(payload: dict[str, Any]) -> dict[str, Any]:
    require_windows()
    # Read at most one extra row to report truncation. Service enumeration is live,
    # so offsets are an observation cursor, not an immutable snapshot guarantee.
    items = [
        service_record(s)
        for s in islice(
            psutil.win_service_iter(), payload["offset"], payload["offset"] + payload["limit"] + 1
        )
    ]
    truncated = len(items) > payload["limit"]
    return {
        "items": items[: payload["limit"]],
        "truncated": truncated,
        "next_offset": payload["offset"] + payload["limit"] if truncated else None,
        "scope": "current_os_account",
        "consistency": "live_observation",
        "arguments_included": False,
    }


def service_get(payload: dict[str, Any]) -> dict[str, Any]:
    require_windows()
    return {
        "service": service_record(psutil.win_service_get(payload["name"])),
        "scope": "current_os_account",
        "arguments_included": False,
    }


def registry_text(key: Any, name: str) -> str | None:
    """Native bounded query: never allocate a registry value's advertised size."""
    from ctypes import wintypes

    query = ctypes.WinDLL("advapi32", use_last_error=True).RegQueryValueExW
    query.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query.restype = wintypes.LONG
    kind, size = wintypes.DWORD(), wintypes.DWORD(4096)
    buffer = ctypes.create_string_buffer(4096)
    code = query(int(key), name, None, ctypes.byref(kind), buffer, ctypes.byref(size))
    if code != 0 or kind.value not in {1, 2} or size.value > 4096 or size.value % 2:
        return None
    try:
        text = buffer.raw[: size.value].decode("utf-16-le").rstrip("\x00")
    except UnicodeError:
        return None
    return text if text and "\x00" not in text else None


def software_list(payload: dict[str, Any]) -> dict[str, Any]:
    require_windows()
    import winreg

    items: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    skipped = 0
    offset, limit = payload["offset"], payload["limit"]
    position = 0
    # All roots/paths/value names are fixed. No caller-supplied registry query.
    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for label, root in [("machine", winreg.HKEY_LOCAL_MACHINE), ("user", winreg.HKEY_CURRENT_USER)]:
        for view, access in [("64", winreg.KEY_WOW64_64KEY), ("32", winreg.KEY_WOW64_32KEY)]:
            try:
                with winreg.OpenKey(root, path, 0, winreg.KEY_READ | access) as container:
                    count = winreg.QueryInfoKey(container)[0]
                    start = max(0, offset - position)
                    for index in range(start, min(count, start + limit + 1 - len(items))):
                        name = winreg.EnumKey(container, index)
                        try:
                            with winreg.OpenKey(
                                container, name, 0, winreg.KEY_READ | access
                            ) as key:
                                record = {
                                    field: registry_text(key, value)
                                    for field, value in [
                                        ("name", "DisplayName"),
                                        ("version", "DisplayVersion"),
                                        ("publisher", "Publisher"),
                                    ]
                                }
                                record.update(
                                    id=f"{label}:{view}:{name}", source=label, registry_view=view
                                )
                                items.append(record)
                        except (OSError, PermissionError):
                            skipped += 1
                            items.append(
                                {"id": f"{label}:{view}:{name}", "availability": "unavailable"}
                            )
                    position += count
                    if len(items) > limit:
                        break
            except FileNotFoundError:
                continue
            except OSError:
                unavailable.append({"source": label, "registry_view": view})
        if len(items) > limit:
            break
    truncated = len(items) > limit
    return {
        "items": items[:limit],
        "truncated": truncated,
        "next_offset": offset + limit if truncated else None,
        "unavailable_sources": unavailable,
        "unavailable_records": skipped,
        "scope": "windows_uninstall_registration",
        "consistency": "live_observation",
        "msix_inventory_included": False,
        "uninstall_commands_included": False,
    }
