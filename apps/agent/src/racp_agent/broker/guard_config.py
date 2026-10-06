"""Private release-only companion pairing, bound to the Broker's native process identity."""

import secrets
from typing import Literal

from pydantic import Field, PrivateAttr
from racp_protocol.models import StrictModel
from racp_sdk.security import token

from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.identity import Identity


class Controller(StrictModel):
    pid: int = Field(ge=1, le=0xFFFFFFFF)
    created: float = Field(gt=0, allow_inf_nan=False)
    sid: str = Field(pattern=r"^S-1-(?:\d+-)*\d+$", max_length=184)
    session: int = Field(ge=0, le=0xFFFFFFFF)
    service_sid: str | None = Field(
        default=None, pattern=r"^S-1-5-80-(?:\d+-){4}\d+$", max_length=184
    )

    def require(self, identity: Identity) -> None:
        if (
            (identity.pid, identity.sid, identity.session) != (self.pid, self.sid, self.session)
            or abs(identity.created - self.created) > 0.000001
            or self.service_sid is not None
            and self.service_sid not in identity.service_sids
        ):
            raise PermissionError("input guardian controller identity mismatch")


class GuardConfig(BrokerConfig):
    launch_backend: Literal["direct", "task"] = "direct"
    cleanup_directory: str | None = Field(default=None, max_length=32768, repr=False)
    marker: int = Field(ge=1, le=0x7FFFFFFF)
    max_hold_ms: int = Field(default=3000, ge=1000, le=5000)
    guardian_pid: int | None = Field(default=None, ge=1, le=0xFFFFFFFF)
    guardian_created: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    controller: Controller | None = None
    controller_principal: str = Field(pattern=r"^S-1-(?:\d+-)*\d+$", max_length=184)
    _caller: Identity | None = PrivateAttr(default=None)

    @property
    def pipe(self) -> str:
        return "\\\\.\\pipe\\LOCAL\\racp-input-guard-" + self.pair_id

    @property
    def agent_acl_sid(self) -> str:
        return self.controller_principal

    def require_agent(self, identity: Identity) -> None:
        try:
            super().require_agent(identity)
        except PermissionError:
            if self.controller is None:
                raise
            self.controller.require(identity)
        self._caller = identity

    def require_broker(self, identity: Identity) -> None:
        super().require_broker(identity)
        if self.guardian_pid is not None and (
            identity.pid != self.guardian_pid
            or self.guardian_created is None
            or abs(identity.created - self.guardian_created) > 0.000001
        ):
            raise PermissionError("input guardian server process mismatch")

    def parent_caller(self) -> bool:
        return self._caller is not None and self._caller.pid == self.agent_pid

    @classmethod
    def pair_guard(
        cls,
        parent: Identity,
        principal: str,
        controller: Controller | None = None,
        job_name: str | None = None,
    ) -> "GuardConfig":
        return cls(
            pair_id=secrets.token_hex(16),
            session_id=parent.session,
            user_sid=parent.sid,
            agent_sid=parent.sid,
            agent_pid=parent.pid,
            agent_created=parent.created,
            agent_session=parent.session,
            secret=token(),
            marker=secrets.randbits(31) or 1,
            controller_principal=principal,
            controller=controller,
            job_name=job_name,
        )
