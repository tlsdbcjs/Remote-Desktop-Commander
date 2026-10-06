"""Bounded one-use enrollment document; secrets never appear in its public preview."""

import hashlib
import re
import ssl
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, AwareDatetime, Field, field_validator
from racp_protocol.models import StrictModel

from racp_sdk.security import require_secure_url

MAX_CONNECTION_BYTES = 32768


def validate_ca_pem(value: str) -> str:
    value = value.strip() + "\n"
    if not re.fullmatch(
        r"(?:-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*)+",
        value,
    ):
        raise ValueError("Connection CA must contain only PEM certificates")
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=value)
    return value


class ConnectionFile(StrictModel):
    version: Literal[1] = 1
    gateway: str = Field(max_length=2048)
    token: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    expires_at: AwareDatetime
    ca_pem: str | None = Field(default=None, max_length=16384, repr=False)

    @field_validator("gateway")
    @classmethod
    def origin(cls, value: str) -> str:
        require_secure_url(value)
        AnyHttpUrl(value)
        parsed = urlsplit(value)
        if parsed.path not in {"", "/"} or parsed.port == 0:
            raise ValueError("Connection Gateway must be an origin")
        return value.rstrip("/")

    @field_validator("ca_pem")
    @classmethod
    def certificate(cls, value: str | None) -> str | None:
        return validate_ca_pem(value) if value is not None else None

    def preview(self, file_sha256: str) -> dict[str, str | None]:
        return {
            "gateway": self.gateway,
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "file_sha256": file_sha256,
            "ca_sha256": hashlib.sha256(self.ca_pem.encode()).hexdigest() if self.ca_pem else None,
        }

    def expired(self) -> bool:
        return self.expires_at <= datetime.now(UTC)
