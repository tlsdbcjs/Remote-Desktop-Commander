import hashlib
import json
import os
import secrets
import ssl
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def token() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical_digest(value: Any) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def require_secure_url(url: str, *, websocket: bool = False) -> None:
    parsed = urlsplit(url)
    secure = "wss" if websocket else "https"
    plain = "ws" if websocket else "http"
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("credentials, query, and fragment are forbidden in endpoint URLs")
    if parsed.scheme == secure and parsed.hostname:
        return
    if parsed.scheme == plain and parsed.hostname in {"127.0.0.1", "::1", "localhost"}:
        return
    raise ValueError("TLS required outside loopback")


def tls_context(ca_file: Path | None) -> ssl.SSLContext | None:
    if ca_file is None:
        return None
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=ca_file)
    return context


def websocket_tls_options(url: str, context: ssl.SSLContext | None) -> dict[str, Any]:
    return {"ssl": context} if url.startswith("wss://") and context is not None else {}


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, value: dict[str, str], *, overwrite: bool = True) -> None:
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError("invalid credential data")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(raw) > 32768:
            raise ValueError("credential data exceeds 32 KiB")
        if os.name == "nt":
            import win32crypt

            raw = win32crypt.CryptProtectData(raw, "RACP", None, None, None, 0)
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(8))
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                os.replace(temporary, self.path)
            elif os.name == "nt":
                os.rename(temporary, self.path)
            else:
                os.link(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self) -> dict[str, str]:
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("credential storage must be a local regular file")
        if os.name != "nt" and self.path.stat().st_mode & 0o077:
            raise PermissionError("credential file must be private (0600)")
        with self.path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("credential storage exceeds 64 KiB")
        if os.name == "nt":
            import pywintypes
            import win32crypt

            try:
                raw = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
            except pywintypes.error as exc:
                raise PermissionError("credential cannot be opened by this OS identity") from exc
        value = json.loads(raw)
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError("invalid credential storage")
        return value
