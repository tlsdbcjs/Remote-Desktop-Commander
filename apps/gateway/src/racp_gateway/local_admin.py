"""Local elevated bootstrap broker for the Windows Gateway service."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from racp_protocol.management import BootstrapReceipt
from racp_protocol.models import timestamp
from racp_sdk.security import digest, token

from racp_gateway.store import GatewayStore

PIPE_NAME = r"\\.\pipe\RACP-Gateway-Admin"
PIPE_MESSAGE_LIMIT = 4096


@dataclass(frozen=True)
class WindowsPrincipal:
    sid: str
    administrator: bool
    session_id: int = 0
    service_sids: tuple[str, ...] = ()


def current_windows_principal() -> WindowsPrincipal:
    import os

    if os.name != "nt":
        raise OSError("Windows principal inspection requires Windows")
    import win32api
    import win32con
    import win32security

    process = win32api.GetCurrentProcess()
    handle = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(handle, win32security.TokenUser)[0]
        sid = win32security.ConvertSidToStringSid(user)
        session = int(win32security.GetTokenInformation(handle, win32security.TokenSessionId))
        groups = win32security.GetTokenInformation(handle, win32security.TokenGroups)
        enabled = {
            win32security.ConvertSidToStringSid(group)
            for group, attributes in groups
            if attributes & 4 and not attributes & 16
        }
        service_sids = tuple(
            value
            for group, attributes in groups
            if attributes & 4
            and (value := win32security.ConvertSidToStringSid(group)).startswith("S-1-5-80-")
            and value != "S-1-5-80-0"
        )
    finally:
        handle.Close()
    return WindowsPrincipal(
        sid=sid,
        administrator="S-1-5-32-544" in enabled,
        session_id=session,
        service_sids=service_sids,
    )


def pipe_windows_principal(handle: int) -> WindowsPrincipal:
    """Read the OS authenticated named-pipe client token; request fields never carry identity."""
    import win32api
    import win32con
    import win32security

    win32security.ImpersonateNamedPipeClient(handle)
    try:
        token_handle = win32security.OpenThreadToken(
            win32api.GetCurrentThread(), win32con.TOKEN_QUERY, True
        )
        try:
            user = win32security.GetTokenInformation(token_handle, win32security.TokenUser)[0]
            sid = win32security.ConvertSidToStringSid(user)
            session = int(
                win32security.GetTokenInformation(token_handle, win32security.TokenSessionId)
            )
            groups = win32security.GetTokenInformation(token_handle, win32security.TokenGroups)
            enabled = {
                win32security.ConvertSidToStringSid(group)
                for group, attributes in groups
                if attributes & 4 and not attributes & 16
            }
        finally:
            token_handle.Close()
    finally:
        win32security.RevertToSelf()
    return WindowsPrincipal(sid=sid, administrator="S-1-5-32-544" in enabled, session_id=session)


class LocalAdminBroker:
    def __init__(self, store: GatewayStore, ttl_seconds: int = 300) -> None:
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("bootstrap TTL must be between 1 and 300 seconds")
        self.store = store
        self.ttl_seconds = ttl_seconds

    def issue_bootstrap(self, caller: WindowsPrincipal) -> BootstrapReceipt:
        if not caller.administrator:
            raise PermissionError("local Gateway bootstrap requires an elevated administrator")
        code = token()
        expires = time.time() + self.ttl_seconds
        with self.store.transaction():
            self.store.db.execute(
                "DELETE FROM gateway_bootstrap WHERE used=1 OR expires<=?", (time.time(),)
            )
            self.store.db.execute(
                "INSERT INTO gateway_bootstrap(digest,caller_sid,expires,used,created_at) "
                "VALUES (?,?,?,0,?)",
                (digest(code), caller.sid, expires, timestamp()),
            )
            self.store.audit(
                "local_bootstrap_created",
                {"context": {"principal_id": "owner_local"}},
                caller_sid=caller.sid,
            )
        expires_at = (
            (datetime.now(UTC) + timedelta(seconds=self.ttl_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        return BootstrapReceipt(
            bootstrap_code=code,
            expires_at=expires_at,
            expires_in_seconds=self.ttl_seconds,
        )

    def consume_bootstrap(self, code: str) -> str:
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT caller_sid,expires,used FROM gateway_bootstrap WHERE digest=?",
                (digest(code),),
            ).fetchone()
            if row is None or row["used"] or float(row["expires"]) <= time.time():
                raise PermissionError("bootstrap code is invalid, expired or already used")
            self.store.db.execute(
                "UPDATE gateway_bootstrap SET used=1 WHERE digest=?", (digest(code),)
            )
        return str(row["caller_sid"])


def issue_from_pipe(store: GatewayStore, handle: int) -> dict[str, Any]:
    """Return only the short-lived bootstrap receipt to an authenticated local pipe caller."""
    return LocalAdminBroker(store).issue_bootstrap(pipe_windows_principal(handle)).model_dump()


class LocalAdminPipeServer:
    """Single-instance local pipe. Windows supplies caller identity; remote clients are rejected."""

    def __init__(self, broker: LocalAdminBroker, pipe_name: str = PIPE_NAME) -> None:
        import pywintypes
        import win32pipe
        import win32security

        self.broker = broker
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            "D:P(A;;GA;;;BA)(A;;GA;;;SY)", win32security.SDDL_REVISION_1
        )
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        self.handle = win32pipe.CreateNamedPipe(
            pipe_name,
            3 | 0x40000000 | 0x80000,
            4 | 2 | 8,
            1,
            PIPE_MESSAGE_LIMIT + 1,
            PIPE_MESSAGE_LIMIT + 1,
            0,
            attributes,
        )

    def accept(self, timeout_ms: int = 250) -> bool:
        import pywintypes
        import win32event
        import win32file
        import win32pipe

        overlap = pywintypes.OVERLAPPED()
        overlap.hEvent = win32event.CreateEvent(None, True, False, None)
        connected = False
        try:
            try:
                status = win32pipe.ConnectNamedPipe(self.handle, overlap)
                if status != 535:
                    if win32event.WaitForSingleObject(overlap.hEvent, timeout_ms) != 0:
                        win32file.CancelIo(self.handle)
                        try:
                            win32file.GetOverlappedResult(self.handle, overlap, True)
                        except (OSError, pywintypes.error):
                            pass
                        return False
                    win32file.GetOverlappedResult(self.handle, overlap, False)
                connected = True
            except pywintypes.error as exc:
                if getattr(exc, "winerror", None) != 535:
                    raise
                connected = True
            status, raw = win32file.ReadFile(self.handle, PIPE_MESSAGE_LIMIT + 1)
            if status not in {0} or not 0 < len(raw) <= PIPE_MESSAGE_LIMIT:
                raise ValueError("invalid local admin message size")
            request = json.loads(raw)
            if not isinstance(request, dict) or set(request) != {"version", "action", "nonce"}:
                raise ValueError("invalid local admin request")
            nonce = request["nonce"]
            if (
                request["version"] != 1
                or request["action"] != "issue_bootstrap"
                or not isinstance(nonce, str)
                or len(nonce) != 64
            ):
                raise ValueError("invalid local admin request")
            receipt = self.broker.issue_bootstrap(pipe_windows_principal(int(self.handle)))
            response = {
                "version": 1,
                "nonce": nonce,
                **receipt.model_dump(),
            }
            encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
            win32file.WriteFile(self.handle, encoded)
            return True
        except (OSError, ValueError, PermissionError, json.JSONDecodeError):
            return False
        finally:
            overlap.hEvent.Close()
            if connected:
                try:
                    win32pipe.DisconnectNamedPipe(self.handle)
                except pywintypes.error:
                    pass

    def close(self) -> None:
        self.handle.Close()


def request_bootstrap(pipe_name: str = PIPE_NAME, timeout_ms: int = 3000) -> BootstrapReceipt:
    import secrets

    import win32file
    import win32pipe

    win32pipe.WaitNamedPipe(pipe_name, timeout_ms)
    handle = win32file.CreateFile(pipe_name, 0xC0000000, 0, None, 3, 0, None)
    try:
        win32pipe.SetNamedPipeHandleState(handle, 2, None, None)
        nonce = secrets.token_hex(32)
        request = json.dumps(
            {"version": 1, "action": "issue_bootstrap", "nonce": nonce}, separators=(",", ":")
        ).encode("utf-8")
        win32file.WriteFile(handle, request)
        status, raw = win32file.ReadFile(handle, PIPE_MESSAGE_LIMIT + 1)
        if status != 0 or len(raw) > PIPE_MESSAGE_LIMIT:
            raise ValueError("invalid local admin response")
        response = json.loads(raw)
        if not isinstance(response, dict) or response.pop("version", None) != 1:
            raise ValueError("invalid local admin response")
        if response.pop("nonce", None) != nonce:
            raise PermissionError("local admin response nonce mismatch")
        return BootstrapReceipt.model_validate(response)
    finally:
        handle.Close()
