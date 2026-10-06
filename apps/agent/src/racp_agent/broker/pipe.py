"""One-request local pipe connections with bounded overlapped I/O and mutual challenges."""

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import validate_tree

from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.identity import client_identity, pipe_pid, process_identity

MAX_MESSAGE = 65536
if os.name == "nt":
    import pywintypes

    NativeError: type[Exception] = pywintypes.error
else:
    NativeError = OSError


def proof(config: BrokerConfig, role: str, server_nonce: str, client_nonce: str) -> str:
    raw = json.dumps([config.version, config.pair_id, role, server_nonce, client_nonce]).encode()
    return hmac.new(config.secret.encode(), raw, hashlib.sha256).hexdigest()


class Pipe:
    def __init__(self, handle: Any, timeout: float = 3) -> None:
        self.handle = handle
        self.deadline = time.monotonic() + timeout

    def complete(self, overlapped: Any) -> int:
        import win32event
        import win32file

        remaining = max(0, int((self.deadline - time.monotonic()) * 1000))
        if win32event.WaitForSingleObject(overlapped.hEvent, remaining) != 0:
            # All I/O is issued and cancelled by this same thread. Keep its buffer alive
            # until cancellation completes; never leave a blocked worker behind.
            win32file.CancelIo(self.handle)
            try:
                win32file.GetOverlappedResult(self.handle, overlapped, True)
            except (OSError, NativeError):
                pass
            raise TimeoutError("Broker pipe deadline elapsed")
        return int(win32file.GetOverlappedResult(self.handle, overlapped, False))

    @staticmethod
    def overlapped() -> Any:
        import pywintypes
        import win32event

        value = pywintypes.OVERLAPPED()
        value.hEvent = win32event.CreateEvent(None, True, False, None)
        return value

    def read(self) -> dict[str, Any]:
        import win32file

        overlap = self.overlapped()
        buffer = win32file.AllocateReadBuffer(MAX_MESSAGE + 1)
        try:
            status, _ = win32file.ReadFile(self.handle, buffer, overlap)
            if status not in {0, 997}:
                raise ValueError("Broker message exceeds limit")
            size = self.complete(overlap)
            if not 0 < size <= MAX_MESSAGE:
                raise ValueError("invalid Broker message size")
            value = json.loads(bytes(buffer[:size]))
            validate_tree(value)
            if not isinstance(value, dict):
                raise ValueError("Broker message must be an object")
            return value
        finally:
            overlap.hEvent.Close()

    def write(self, value: dict[str, Any]) -> None:
        import win32file

        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_MESSAGE:
            raise ValueError("Broker message exceeds limit")
        overlap = self.overlapped()
        try:
            win32file.WriteFile(self.handle, raw, overlap)
            if self.complete(overlap) != len(raw):
                raise OSError("partial Broker write")
        finally:
            overlap.hEvent.Close()

    def close(self) -> None:
        self.handle.Close()


class PipeServer:
    def __init__(self, config: BrokerConfig) -> None:
        import pywintypes
        import win32pipe
        import win32security

        config.require_broker(process_identity(os.getpid()))
        self.config = config
        self.identity = process_identity(os.getpid())
        # Client gets data read/write + synchronize, not CREATE_PIPE_INSTANCE.
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;;GA;;;{config.user_sid})(A;;0x100103;;;{config.agent_acl_sid})",
            win32security.SDDL_REVISION_1,
        )
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        self.handle = win32pipe.CreateNamedPipe(
            config.pipe,
            3 | 0x40000000 | 0x80000,  # DUPLEX | OVERLAPPED | FIRST_PIPE_INSTANCE
            4 | 2 | 8,  # MESSAGE | READMODE_MESSAGE | REJECT_REMOTE_CLIENTS
            1,
            MAX_MESSAGE + 1,
            MAX_MESSAGE + 1,
            0,
            attributes,
        )

    def accept(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> bool:
        import win32pipe

        pipe = Pipe(self.handle, timeout=0.25)
        overlap = pipe.overlapped()
        connected = False
        try:
            try:
                status = win32pipe.ConnectNamedPipe(self.handle, overlap)
                if status != 535:  # Client connected before ConnectNamedPipe; no pending I/O.
                    pipe.complete(overlap)
                connected = True
            except (OSError, NativeError) as exc:
                if getattr(exc, "winerror", None) != 535:  # ERROR_PIPE_CONNECTED
                    raise
                connected = True
            pipe.deadline = time.monotonic() + 3
            # Read first so OS impersonation has an actual client message token.
            initial = pipe.read()
            self.config.require_agent(client_identity(int(self.handle)))
            nonce = initial.get("nonce")
            if initial.keys() != {"version", "nonce"} or initial["version"] != self.config.version:
                raise ValueError("Broker protocol version mismatch")
            if not isinstance(nonce, str) or len(nonce) != 64:
                raise ValueError("invalid Broker challenge")
            server_nonce = secrets.token_hex(32)
            pipe.write(
                {"nonce": server_nonce, "proof": proof(self.config, "broker", server_nonce, nonce)}
            )
            request = pipe.read()
            expected = proof(self.config, "agent", server_nonce, nonce)
            if not isinstance(request.get("proof"), str) or not hmac.compare_digest(
                request["proof"], expected
            ):
                raise PermissionError("Broker challenge failed")
            if request.keys() != {"proof", "request"} or not isinstance(request["request"], dict):
                raise ValueError("invalid Broker request")
            context = request["request"].get("context", {})
            if not isinstance(context, dict):
                raise ValueError("invalid Broker context")
            budget = context.get("timeout_ms", 3000)
            if not isinstance(budget, int) or not 1 <= budget <= 30000:
                raise ValueError("invalid Broker deadline")
            pipe.deadline = time.monotonic() + budget / 1000
            pipe.write(handler(request["request"]))
            # DisconnectNamedPipe discards unread data. Keep the instance connected
            # until the client has consumed the response, without an unbounded flush.
            if pipe.read() != {"received": True}:
                raise ValueError("invalid Broker response receipt")
            return True
        except (
            OSError,
            NativeError,
            ValueError,
            RecursionError,
            PermissionError,
            TimeoutError,
            RACPError,
        ):
            return False
        finally:
            overlap.hEvent.Close()
            if connected:
                try:
                    win32pipe.DisconnectNamedPipe(self.handle)
                except NativeError:
                    pass

    def close(self) -> None:
        self.handle.Close()


def request(config: BrokerConfig, value: dict[str, Any], timeout: float = 3) -> dict[str, Any]:
    import win32file
    import win32pipe

    config.require_agent(process_identity(os.getpid()))
    deadline = time.monotonic() + timeout
    while True:
        try:
            handle = win32file.CreateFile(
                config.pipe,
                0x100103,
                0,
                None,
                3,
                0x40000000 | 0x100000 | 0x20000,
                None,
                # OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION (query only).
            )
            break
        except NativeError as exc:
            if getattr(exc, "winerror", None) not in {2, 231}:
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError("Broker pipe unavailable") from None
            try:
                win32pipe.WaitNamedPipe(
                    config.pipe, min(50, max(1, int((deadline - time.monotonic()) * 1000)))
                )
            except NativeError as wait_error:
                if getattr(wait_error, "winerror", None) not in {2, 121, 231}:
                    raise
                time.sleep(0.005)
    pipe = Pipe(handle, max(0.001, deadline - time.monotonic()))
    try:
        win32pipe.SetNamedPipeHandleState(handle, 2, None, None)
        config.require_broker(process_identity(pipe_pid(int(handle), server=True)))
        nonce = secrets.token_hex(32)
        pipe.write({"version": config.version, "nonce": nonce})
        response = pipe.read()
        server_nonce = response.get("nonce")
        if not isinstance(server_nonce, str) or len(server_nonce) != 64:
            raise PermissionError("invalid Broker server challenge")
        expected = proof(config, "broker", server_nonce, nonce)
        if not isinstance(response.get("proof"), str) or not hmac.compare_digest(
            response["proof"], expected
        ):
            raise PermissionError("Broker server challenge failed")
        pipe.write({"proof": proof(config, "agent", server_nonce, nonce), "request": value})
        result = pipe.read()
        try:
            pipe.write({"received": True})
        except (OSError, NativeError, TimeoutError):
            pass  # Response is already fully consumed; never lose a known result.
        return result
    finally:
        pipe.close()
