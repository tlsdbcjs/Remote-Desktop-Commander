"""Explicit direct TLS ingress; forwarded headers never establish transport trust."""

import ipaddress
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from racp_sdk.security import require_secure_url


def checked_origin(value: str) -> str:
    require_secure_url(value)
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"} or parsed.port == 0:
        raise ValueError("public origin must contain only scheme, host and optional port")
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    if any(char in hostname for char in " *\\\t\r\n"):
        raise ValueError("public origin must use an exact hostname")
    authority = "[" + hostname + "]" if ":" in hostname else hostname
    if parsed.port is not None and parsed.port != (443 if parsed.scheme == "https" else 80):
        authority += ":" + str(parsed.port)
    return parsed.scheme + "://" + authority


@dataclass(frozen=True)
class GatewayNetwork:
    host: str = "127.0.0.1"
    port: int = 8765
    public_origin: str | None = None
    certificate: Path | None = None
    private_key: Path | None = None

    def validate(self) -> "GatewayNetwork":
        if not 1 <= self.port <= 65535:
            raise ValueError("listen port must be between 1 and 65535")
        try:
            loopback = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            loopback = self.host == "localhost"
        if (self.certificate is None) != (self.private_key is None):
            raise ValueError("TLS certificate and private key must be configured together")
        if not loopback and (self.certificate is None or self.public_origin is None):
            raise ValueError("non-loopback listener requires TLS and an explicit public origin")
        if self.public_origin is not None:
            origin = checked_origin(self.public_origin)
            if urlsplit(origin).scheme != ("https" if self.certificate else "http"):
                raise ValueError("public origin scheme must match the direct listener transport")
        if self.certificate is not None and self.private_key is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.certificate, self.private_key)
        return self

    def uvicorn_options(self) -> dict[str, object]:
        self.validate()
        options: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "proxy_headers": False,
        }
        if self.certificate is not None and self.private_key is not None:
            options.update(
                ssl_certfile=str(self.certificate),
                ssl_keyfile=str(self.private_key),
                ssl_version=ssl.PROTOCOL_TLS_SERVER,
                ssl_ciphers="ECDHE+AESGCM:ECDHE+CHACHA20",
            )
        return options
