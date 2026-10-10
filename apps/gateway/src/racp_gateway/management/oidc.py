"""Pinned OIDC authorization-code/PKCE login for management users."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from pydantic import Field
from racp_domain.models import RACPError
from racp_protocol.management import ManagementPrincipal, OidcBeginView
from racp_protocol.models import StrictModel
from racp_sdk.security import digest, require_secure_url, tls_context

from racp_gateway.console_auth import ConsoleAuth
from racp_gateway.management.users import UserManagement
from racp_gateway.store import GatewayStore


class OidcManagementConfig(StrictModel):
    issuer: str = Field(max_length=2048)
    authorization_endpoint: str = Field(max_length=2048)
    token_endpoint: str = Field(max_length=2048)
    jwks_uri: str = Field(max_length=2048)
    client_id: str = Field(min_length=1, max_length=512)
    redirect_uri: str = Field(max_length=2048)
    client_secret_file: Path | None = None
    ca_file: Path | None = None
    algorithms: list[str] = Field(default=["RS256"], min_length=1, max_length=2)

    def model_post_init(self, context: object) -> None:
        for value in (
            self.issuer,
            self.authorization_endpoint,
            self.token_endpoint,
            self.jwks_uri,
            self.redirect_uri,
        ):
            require_secure_url(value)
        if not self.redirect_uri.startswith("https://"):
            raise ValueError("OIDC callback requires HTTPS")
        if any(algorithm not in {"RS256", "ES256"} for algorithm in self.algorithms):
            raise ValueError("unsupported OIDC signing algorithm")


@dataclass(frozen=True)
class SessionReceipt:
    credential: str
    principal: ManagementPrincipal


class OidcLogin:
    def __init__(
        self,
        config: OidcManagementConfig,
        store: GatewayStore,
        users: UserManagement,
        console_auth: ConsoleAuth,
    ) -> None:
        self.config = config
        self.store = store
        self.users = users
        self.console_auth = console_auth
        self.verify = tls_context(config.ca_file) or True
        self.keys: dict[str, jwt.PyJWK] = {}
        self.keys_expires = 0.0

    def begin(self) -> OidcBeginView:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        with self.store.transaction():
            self.store.db.execute(
                "DELETE FROM oidc_login_state WHERE used=1 OR expires<=?", (time.time(),)
            )
            self.store.db.execute(
                "INSERT INTO oidc_login_state VALUES (?,?,?,?,?,0)",
                (digest(state), nonce, verifier, self.config.redirect_uri, time.time() + 300),
            )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": "openid profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return OidcBeginView(
            authorization_url=self.config.authorization_endpoint + "?" + query,
            state=state,
        )

    async def _refresh_keys(self) -> None:
        async with httpx.AsyncClient(
            verify=self.verify, follow_redirects=False, trust_env=False, timeout=5
        ) as http:
            response = await http.get(self.config.jwks_uri)
            response.raise_for_status()
        if len(response.content) > 256 * 1024:
            raise RACPError("UNAUTHENTICATED", "OIDC signing key response is too large")
        document = response.json()
        entries = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
            raise RACPError("UNAUTHENTICATED", "invalid OIDC signing key response")
        keys: dict[str, jwt.PyJWK] = {}
        for entry in entries:
            if not isinstance(entry, dict) or any(name in entry for name in ("d", "p", "q", "oth")):
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > 128:
                continue
            key = jwt.PyJWK.from_dict(entry)
            if key.algorithm_name in self.config.algorithms:
                keys[kid] = key
        if not keys:
            raise RACPError("UNAUTHENTICATED", "OIDC provider has no approved signing keys")
        self.keys = keys
        self.keys_expires = time.monotonic() + 300

    def _client_secret(self) -> str | None:
        path = self.config.client_secret_file
        if path is None:
            return None
        if not path.is_absolute() or str(path).startswith("\\\\"):
            raise ValueError("OIDC client secret must be an absolute local path")
        raw = path.read_text(encoding="utf-8").strip()
        if not raw or len(raw) > 4096:
            raise ValueError("invalid OIDC client secret")
        return raw

    async def complete(self, code: str, state: str) -> SessionReceipt:
        if not code or len(code) > 8192 or not state or len(state) > 256:
            raise RACPError("UNAUTHENTICATED", "invalid OIDC callback")
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT * FROM oidc_login_state WHERE state_digest=?", (digest(state),)
            ).fetchone()
            if row is None or row["used"] or float(row["expires"]) <= time.time():
                raise RACPError("UNAUTHENTICATED", "OIDC state is invalid, expired or used")
            if str(row["redirect_uri"]) != self.config.redirect_uri:
                raise RACPError("UNAUTHENTICATED", "OIDC callback URI changed")
            self.store.db.execute(
                "UPDATE oidc_login_state SET used=1 WHERE state_digest=?", (digest(state),)
            )
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "code_verifier": str(row["verifier"]),
        }
        secret = self._client_secret()
        if secret is not None:
            payload["client_secret"] = secret
        try:
            async with httpx.AsyncClient(
                verify=self.verify, follow_redirects=False, trust_env=False, timeout=5
            ) as http:
                response = await http.post(self.config.token_endpoint, data=payload)
                response.raise_for_status()
            if len(response.content) > 128 * 1024:
                raise ValueError("OIDC token response exceeds limit")
            document = response.json()
            encoded = document.get("id_token") if isinstance(document, dict) else None
            if not isinstance(encoded, str) or len(encoded) > 16384:
                raise ValueError("OIDC response has no bounded ID token")
            header = jwt.get_unverified_header(encoded)
            kid = header.get("kid")
            if header.get("alg") not in self.config.algorithms or not isinstance(kid, str):
                raise ValueError("OIDC ID token algorithm is not allowed")
            if time.monotonic() >= self.keys_expires or kid not in self.keys:
                await self._refresh_keys()
            key = self.keys.get(kid)
            if key is None or key.algorithm_name != header.get("alg"):
                raise ValueError("OIDC signing key is unavailable")
            claims = jwt.decode(
                encoded,
                key.key,
                algorithms=self.config.algorithms,
                issuer=self.config.issuer,
                audience=self.config.client_id,
                options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
            )
            if claims.get("nonce") != row["nonce"]:
                raise ValueError("OIDC nonce differs")
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject:
                raise ValueError("OIDC subject is invalid")
            principal = self.users.principal_for_identity(self.config.issuer, subject)
            credential, _ = self.console_auth.create_session(principal.actor_id)
            self.store.audit(
                "management_oidc_login",
                {"context": {"principal_id": principal.actor_id}},
                issuer=urlsplit(self.config.issuer).hostname or "",
            )
            return SessionReceipt(credential=credential, principal=principal)
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise RACPError("UNAUTHENTICATED", "OIDC login failed") from exc
