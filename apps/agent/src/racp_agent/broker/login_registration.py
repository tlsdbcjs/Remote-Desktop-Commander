"""User-logon Broker registration using OS pipe identity, without WTS user tokens or passwords."""

import asyncio
import concurrent.futures
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field
from racp_domain.models import RACPError
from racp_protocol.models import Identifier, StrictModel

from racp_agent.broker.access import grant_own_access
from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.identity import Identity, client_identity, pipe_pid, process_identity
from racp_agent.broker.job import create_broker_job
from racp_agent.broker.peer_process import PeerProcess
from racp_agent.broker.pipe import MAX_MESSAGE, NativeError, Pipe, proof


class LoginEndpoint(StrictModel):
    version: int = Field(default=1, ge=1, le=1)
    device_id: Identifier
    agent_sid: str = Field(pattern=r"^S-1-(?:\d+-)*\d+$", max_length=184)
    service_sid: str | None = Field(
        default=None, pattern=r"^S-1-5-80-(?:\d+-){4}\d+$", max_length=184
    )

    @property
    def pipe(self) -> str:
        return "\\\\.\\pipe\\LOCAL\\racp-login-" + self.device_id

    @property
    def principal(self) -> str:
        return self.service_sid or self.agent_sid

    def check_agent(self, identity: Identity) -> None:
        if (
            identity.sid != self.agent_sid
            or self.service_sid is not None
            and self.service_sid not in identity.service_sids
        ):
            raise PermissionError("login pipe Agent identity differs from installed endpoint")
        if identity.sid == "S-1-5-18":
            raise PermissionError("LocalSystem is not the RACP Agent's default identity")
        if identity.session == 0 and self.service_sid is None:
            raise PermissionError(
                "SCM login registration requires the specific installed service SID"
            )
        if self.service_sid is not None and identity.administrator:
            raise PermissionError(
                "RACP login registration requires a nonadministrator service identity"
            )

    def save(self, path: Path, users: tuple[str, ...]) -> None:
        import win32security

        from racp_agent.providers.paths import is_link

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if is_link(path.parent.lstat()) or path.exists() and is_link(path.lstat()):
            raise PermissionError("login endpoint path cannot be a link")
        temporary = path.with_name(path.name + "." + secrets.token_hex(16))
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(self.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f"D:P(A;;FA;;;{self.principal})(A;;FA;;;BA)"
                + "".join(f"(A;;FR;;;{sid})" for sid in users),
                win32security.SDDL_REVISION_1,
            )
            win32security.SetFileSecurity(
                str(temporary),
                win32security.DACL_SECURITY_INFORMATION
                | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                descriptor,
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class LoginHello(StrictModel):
    version: int = Field(ge=1, le=1)
    nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    device_id: Identifier
    session_id: int = Field(ge=1, le=0xFFFFFFFF)


@dataclass
class LoginGrant:
    config: BrokerConfig
    peer: PeerProcess
    job: Any
    transferred: bool = False

    def commit(self) -> None:
        self.transferred = True

    def close(self) -> None:
        if self.transferred:
            return
        import win32job

        win32job.TerminateJobObject(self.job, 1)
        self.job.Close()
        self.peer.close()


class LoginServer:
    def __init__(self, endpoint: LoginEndpoint, users: tuple[str, ...]) -> None:
        import pywintypes
        import win32pipe
        import win32security

        if not 1 <= len(users) <= 16 or len(set(users)) != len(users):
            raise ValueError("login registration requires 1–16 distinct installed user SIDs")
        self.endpoint, self.users = endpoint, frozenset(users)
        self.identity = process_identity(os.getpid())
        endpoint.check_agent(self.identity)
        for sid in users:
            win32security.ConvertStringSidToSid(sid)
        self.restore = grant_own_access(users, managed_broker=False)
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;;GA;;;{endpoint.principal})"
            + "".join(f"(A;;0x100103;;;{sid})" for sid in users),
            win32security.SDDL_REVISION_1,
        )
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        try:
            self.handle = win32pipe.CreateNamedPipe(
                endpoint.pipe,
                3 | 0x40000000 | 0x80000,
                4 | 2 | 8,
                1,
                MAX_MESSAGE + 1,
                MAX_MESSAGE + 1,
                0,
                attributes,
            )
        except BaseException:
            self.restore()
            raise

    def validate_peer(self, hello: LoginHello, peer: Identity) -> None:
        if (
            hello.device_id != self.endpoint.device_id
            or peer.sid not in self.users
            or peer.session != hello.session_id
        ):
            raise PermissionError("login Broker user/Device/session binding denied")
        if peer.session == 0 or peer.pid == self.identity.pid:
            raise PermissionError("login Broker must be a separate user-session process")

    def accept(self, adopt: Callable[[LoginGrant], None]) -> bool:
        import win32api
        import win32job
        import win32pipe

        pipe, overlap = Pipe(self.handle, 0.25), Pipe.overlapped()
        connected, grant = False, None
        try:
            try:
                status = win32pipe.ConnectNamedPipe(self.handle, overlap)
                if status != 535:
                    pipe.complete(overlap)
            except NativeError as exc:
                if getattr(exc, "winerror", None) != 535:
                    raise
            connected = True
            pipe.deadline = time.monotonic() + 3
            hello = LoginHello.model_validate(pipe.read())
            peer = client_identity(int(self.handle))
            self.validate_peer(hello, peer)
            # Pin the authenticated process and assign it before it creates a GUI backend.
            handle = win32api.OpenProcess(0x101101, False, peer.pid)
            config = BrokerConfig.pair(self.identity, peer, secrets.token_hex(16))
            config.agent_service_sid = self.endpoint.service_sid
            try:
                job = create_broker_job(config)
            except BaseException:
                handle.Close()
                raise
            try:
                if process_identity(peer.pid) != peer:
                    raise PermissionError("login Broker process identity changed")
                win32job.AssignProcessToJobObject(job, handle)
                grant = LoginGrant(config, PeerProcess(peer.pid, handle), job)
            except BaseException:
                handle.Close()
                job.Close()
                raise
            server_nonce = secrets.token_hex(32)
            pipe.write(
                {"nonce": hello.nonce, "server_nonce": server_nonce, "config": config.model_dump()}
            )
            receipt = pipe.read()
            if receipt != {
                "received": True,
                "pair_id": config.pair_id,
                "proof": proof(config, "login-broker", server_nonce, hello.nonce),
            }:
                raise PermissionError("login registration receipt mismatch")
            adopt(grant)
            if not grant.transferred:
                raise RuntimeError("login registration was not transferred to a supervisor")
            return True
        except (NativeError, OSError, ValueError, PermissionError, RACPError, TimeoutError):
            return False
        finally:
            overlap.hEvent.Close()
            if connected:
                try:
                    win32pipe.DisconnectNamedPipe(self.handle)
                except NativeError:
                    pass
            if grant is not None:
                grant.close()

    def close(self) -> None:
        try:
            self.handle.Close()
        finally:
            self.restore()


class LoginRegistrar:
    def __init__(
        self, endpoint: LoginEndpoint, users: tuple[str, ...], adopt: Callable[[LoginGrant], Any]
    ) -> None:
        self.server = LoginServer(endpoint, users)
        self.adopt = adopt
        self.stopping = threading.Event()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()

        def transfer(grant: LoginGrant) -> None:
            future = asyncio.run_coroutine_threadsafe(self.adopt(grant), loop)
            try:
                future.result(timeout=3)
            except concurrent.futures.TimeoutError:
                if not grant.transferred:
                    future.cancel()
                    raise TimeoutError("login adoption deadline elapsed") from None

        def serve() -> None:
            while not self.stopping.is_set():
                self.server.accept(transfer)

        worker = asyncio.create_task(asyncio.to_thread(serve))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.stopping.set()
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            await worker
            raise
        finally:
            self.server.close()


def register(endpoint: LoginEndpoint) -> BrokerConfig:
    import win32file
    import win32pipe

    user = process_identity(os.getpid())
    if user.session == 0:
        raise RACPError(
            "SESSION_UNAVAILABLE", "login Broker requires a user session", layer="broker"
        )
    handle = win32file.CreateFile(endpoint.pipe, 0x100103, 0, None, 3, 0x40000000 | 0x120000, None)
    pipe = Pipe(handle, 3)
    try:
        win32pipe.SetNamedPipeHandleState(handle, 2, None, None)
        agent = process_identity(pipe_pid(int(handle), server=True))
        endpoint.check_agent(agent)
        nonce = secrets.token_hex(32)
        pipe.write(
            LoginHello(
                version=1, nonce=nonce, device_id=endpoint.device_id, session_id=user.session
            ).model_dump()
        )
        response = pipe.read()
        if response.keys() != {"nonce", "server_nonce", "config"} or response["nonce"] != nonce:
            raise PermissionError("login registration challenge mismatch")
        if not isinstance(response["server_nonce"], str) or len(response["server_nonce"]) != 64:
            raise PermissionError("invalid login registration server nonce")
        config = BrokerConfig.model_validate(response["config"])
        config.require_agent(agent)
        config.require_broker(user)
        if config.agent_service_sid != endpoint.service_sid:
            raise PermissionError("login registration service SID mismatch")
        pipe.write(
            {
                "received": True,
                "pair_id": config.pair_id,
                "proof": proof(config, "login-broker", response["server_nonce"], nonce),
            }
        )
        return config
    finally:
        pipe.close()
