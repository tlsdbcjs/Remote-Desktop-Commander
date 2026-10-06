"""Readonly WTS state and logon identity. Never obtains user tokens or changes sessions."""

import ctypes
from ctypes import wintypes
from typing import Any

from racp_agent.broker.pipe import NativeError

windows_ctypes: Any = ctypes


class WtsLevel1(ctypes.Structure):
    _fields_ = [
        ("session", wintypes.ULONG),
        ("state", ctypes.c_int),
        ("flags", wintypes.LONG),
        ("station", wintypes.WCHAR * 33),
        ("user", wintypes.WCHAR * 21),
        ("domain", wintypes.WCHAR * 18),
        ("logon", ctypes.c_longlong),
        ("connect", ctypes.c_longlong),
        ("disconnect", ctypes.c_longlong),
        ("last_input", ctypes.c_longlong),
        ("current", ctypes.c_longlong),
        ("incoming_bytes", wintypes.DWORD),
        ("outgoing_bytes", wintypes.DWORD),
        ("incoming_frames", wintypes.DWORD),
        ("outgoing_frames", wintypes.DWORD),
        ("incoming_compressed", wintypes.DWORD),
        ("outgoing_compressed", wintypes.DWORD),
    ]


class WtsData(ctypes.Union):
    _fields_ = [("one", WtsLevel1)]


class WtsInfo(ctypes.Structure):
    _fields_ = [("level", wintypes.DWORD), ("data", WtsData)]


def locked(session_id: int) -> bool | None:
    wts = windows_ctypes.WinDLL("wtsapi32", use_last_error=True)
    wts.WTSQuerySessionInformationW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    wts.WTSFreeMemory.argtypes = [wintypes.LPVOID]
    value, size = wintypes.LPVOID(), wintypes.DWORD()
    if not wts.WTSQuerySessionInformationW(
        None, session_id, 25, ctypes.byref(value), ctypes.byref(size)
    ):
        return None
    try:
        if size.value < ctypes.sizeof(WtsInfo):
            return None
        info = ctypes.cast(value, ctypes.POINTER(WtsInfo)).contents
        if (
            info.level != 1
            or info.data.one.session != session_id
            or info.data.one.flags not in {0, 1}
        ):
            return None
        # Supported Windows 10/11; Windows 7's reversed flags are outside our OS contract.
        return bool(info.data.one.flags == 0)
    finally:
        wts.WTSFreeMemory(value)


def query(session_id: int) -> dict[str, Any]:
    import win32security
    import win32ts

    try:
        state = win32ts.WTSQuerySessionInformation(None, session_id, win32ts.WTSConnectState)
        user = win32ts.WTSQuerySessionInformation(None, session_id, win32ts.WTSUserName)
        domain = win32ts.WTSQuerySessionInformation(None, session_id, win32ts.WTSDomainName)
    except NativeError:
        return {
            "exists": False,
            "identity_known": False,
            "logged_on": False,
            "session_id": session_id,
            "state": "unavailable",
            "user_sid": None,
            "locked": None,
        }
    name = f"{domain}\\{user}" if domain and user else str(user)
    sid = None
    if user:
        try:
            principal, _, _ = win32security.LookupAccountName(None, name)
            sid = win32security.ConvertSidToStringSid(principal)
        except NativeError:
            pass
    return {
        "exists": True,
        "identity_known": not user or sid is not None,
        "logged_on": bool(user),
        "session_id": session_id,
        "state": {0: "active", 4: "disconnected"}.get(state, "inactive"),
        "user_name": name,
        "user_sid": sid,
        "locked": locked(session_id),
    }


def same_logon(state: dict[str, Any], user_sid: str) -> bool:
    # A failed query is not proof of logoff. A known empty or changed user is proof.
    return not state["identity_known"] or state["logged_on"] and state["user_sid"] == user_sid
