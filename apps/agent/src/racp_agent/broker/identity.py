"""OS-derived peer identity. Client-provided PIDs, SIDs and tokens are never trusted."""

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

import psutil
from racp_domain.models import RACPError

windows_ctypes: Any = ctypes


@dataclass(frozen=True)
class Identity:
    pid: int
    created: float
    sid: str
    session: int
    integrity: int
    service_sids: tuple[str, ...] = ()
    administrator: bool = False


def process_identity(pid: int) -> Identity:
    if os.name != "nt":
        raise RACPError("CAPABILITY_UNAVAILABLE", "Session Broker requires Windows", layer="broker")
    import win32api
    import win32con
    import win32event
    import win32security

    process = win32api.OpenProcess(0x101000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    try:
        token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
        try:
            sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
            session = int(win32security.GetTokenInformation(token, win32security.TokenSessionId))
            integrity_sid = win32security.GetTokenInformation(token, 25)[0]
            integrity = int(win32security.ConvertSidToStringSid(integrity_sid).split("-")[-1])
            groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
            enabled = {
                win32security.ConvertSidToStringSid(group)
                for group, attributes in groups
                if attributes & 4 and not attributes & 16
            }
            service_sids = tuple(
                value
                for group, attributes in groups
                if attributes & 4  # SE_GROUP_ENABLED
                and (value := win32security.ConvertSidToStringSid(group)).startswith("S-1-5-80-")
                and value != "S-1-5-80-0"  # All Services is never a specific service identity.
            )
        finally:
            token.Close()
        # The process handle prevents PID reuse during the token/creation-time comparison.
        created = psutil.Process(pid).create_time()
        if win32event.WaitForSingleObject(process, 0) == 0:
            raise RACPError("SESSION_UNAVAILABLE", "peer exited", layer="broker")
        return Identity(
            pid, created, sid, session, integrity, service_sids, "S-1-5-32-544" in enabled
        )
    finally:
        process.Close()


def pipe_pid(handle: int, *, server: bool) -> int:
    kernel = windows_ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.GetNamedPipeServerProcessId if server else kernel.GetNamedPipeClientProcessId
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    function.restype = wintypes.BOOL
    value = wintypes.ULONG()
    if not function(handle, ctypes.byref(value)):
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())
    return int(value.value)


def client_identity(handle: int) -> Identity:
    import win32api
    import win32con
    import win32security

    identity = process_identity(pipe_pid(handle, server=False))
    # Impersonate only the OS pipe client to query its token, then immediately revert.
    # No GUI operation runs impersonated and no supplied token is accepted.
    win32security.ImpersonateNamedPipeClient(handle)
    try:
        token = win32security.OpenThreadToken(
            win32api.GetCurrentThread(), win32con.TOKEN_QUERY, True
        )
        try:
            sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
            session = int(win32security.GetTokenInformation(token, win32security.TokenSessionId))
        finally:
            token.Close()
    finally:
        win32security.RevertToSelf()
    if sid != identity.sid or session != identity.session:
        raise RACPError(
            "PERMISSION_DENIED", "pipe token differs from process identity", layer="broker"
        )
    return identity
