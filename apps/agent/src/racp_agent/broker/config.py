"""Short-lived pairing configuration, protected by the two explicit OS identities."""

import json
import os
from pathlib import Path
from typing import Self

from pydantic import Field
from racp_protocol.models import StrictModel
from racp_sdk.security import token

from racp_agent.broker.identity import Identity
from racp_agent.providers.paths import is_link


class BrokerConfig(StrictModel):
    version: int = Field(default=1, ge=1, le=1)
    pair_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    session_id: int = Field(ge=1, le=0xFFFFFFFF)
    user_sid: str = Field(pattern=r"^S-1-(?:\d+-)*\d+$", max_length=184)
    agent_sid: str = Field(pattern=r"^S-1-(?:\d+-)*\d+$", max_length=184)
    agent_service_sid: str | None = Field(
        default=None, pattern=r"^S-1-5-80-(?:\d+-){4}\d+$", max_length=184
    )
    agent_pid: int = Field(ge=1, le=0xFFFFFFFF)
    agent_created: float = Field(gt=0, allow_inf_nan=False)
    agent_session: int = Field(ge=0, le=0xFFFFFFFF)
    secret: str = Field(min_length=43, max_length=128, repr=False)
    job_name: str | None = Field(default=None, pattern=r"^Global\\RACP-Broker-Job-[a-f0-9]{32}$")

    @property
    def agent_acl_sid(self) -> str:
        return self.agent_service_sid or self.agent_sid

    @property
    def pipe(self) -> str:
        return "\\\\.\\pipe\\LOCAL\\racp-session-" + self.pair_id

    def require_agent(self, identity: Identity) -> None:
        if (
            identity.pid != self.agent_pid
            or abs(identity.created - self.agent_created) > 0.000001
            or identity.sid != self.agent_sid
            or identity.session != self.agent_session
            or self.agent_service_sid is not None
            and self.agent_service_sid not in identity.service_sids
        ):
            raise PermissionError("Broker peer is not the paired Agent process")

    def require_broker(self, identity: Identity) -> None:
        if identity.sid != self.user_sid or identity.session != self.session_id:
            raise PermissionError("Broker peer user/session differs from pairing")

    def save(self, directory: Path) -> Path:
        if os.name != "nt":
            raise OSError("Broker pairing requires Windows")
        import win32file
        import win32security

        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if is_link(directory.lstat()):
            raise PermissionError("Broker pairing root must not be a link")
        root = directory.resolve(strict=True)
        acl = win32security.ACL()
        for value in {self.agent_acl_sid, self.user_sid}:
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION_DS,
                3,  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
                win32file.FILE_ALL_ACCESS,
                win32security.ConvertStringSidToSid(value),
            )
        win32security.SetNamedSecurityInfo(
            str(root),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )
        path = root / (self.pair_id + ".json")
        with path.open("x", encoding="utf-8") as stream:
            stream.write(self.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        return path

    @classmethod
    def load(cls, path: Path) -> Self:
        if is_link(path.lstat()) or path.stat().st_size > 4096:
            raise PermissionError("invalid Broker pairing file")
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def pair(cls, agent: Identity, user: Identity, pair_id: str) -> "BrokerConfig":
        return cls(
            pair_id=pair_id,
            session_id=user.session,
            user_sid=user.sid,
            agent_sid=agent.sid,
            agent_pid=agent.pid,
            agent_created=agent.created,
            agent_session=agent.session,
            secret=token(),
        )
