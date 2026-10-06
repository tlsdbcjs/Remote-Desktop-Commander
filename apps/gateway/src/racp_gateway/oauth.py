"""MCP resource server for an administrator-pinned external OAuth provider."""

import asyncio
import json
import re
import stat
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, ListToolsResult, TextContent
from pydantic import AnyHttpUrl, Field, model_validator
from racp_protocol.models import StrictModel
from racp_protocol.provider_models import SENSITIVE_PROCESS_READS
from racp_protocol.registry import REGISTRY
from racp_sdk.security import require_secure_url, tls_context

from racp_gateway.network import checked_origin


class OAuthResourceConfig(StrictModel):
    version: Literal[1] = 1
    issuer: str = Field(min_length=1, max_length=2048)
    jwks_uri: str = Field(min_length=1, max_length=2048)
    owner_subject: str = Field(min_length=1, max_length=256)
    client_ids: list[str] = Field(min_length=1, max_length=16)
    read_scope: str = Field(default="racp.read", pattern=r"^[A-Za-z0-9:._-]{1,128}$")
    execute_scope: str = Field(default="racp.execute", pattern=r"^[A-Za-z0-9:._-]{1,128}$")
    algorithms: list[Literal["RS256", "ES256"]] = Field(
        default=["RS256"], min_length=1, max_length=2
    )
    ca_file: Path | None = None
    jwks_ttl_seconds: int = Field(default=300, ge=30, le=600)
    max_token_seconds: int = Field(default=3600, ge=60, le=86400)

    @model_validator(mode="after")
    def pinned_provider(self) -> "OAuthResourceConfig":
        for value in [self.issuer, self.jwks_uri]:
            AnyHttpUrl(value)
            require_secure_url(value)
            if not value.startswith("https://"):
                raise ValueError("OAuth provider endpoints require HTTPS")
        if (
            len(set(self.client_ids)) != len(self.client_ids)
            or any(not value or len(value) > 2048 for value in self.client_ids)
            or self.read_scope == self.execute_scope
            or not self.algorithms
            or len(set(self.algorithms)) != len(self.algorithms)
        ):
            raise ValueError("invalid OAuth client, scope or algorithm configuration")
        if self.ca_file is not None and not self.ca_file.is_absolute():
            raise ValueError("OAuth CA file must be absolute")
        return self


def load_oauth_config(path: Path | None) -> OAuthResourceConfig | None:
    if path is None:
        return None
    if not path.is_absolute() or str(path).startswith("\\\\"):
        raise ValueError("OAuth configuration must be an explicit absolute local path")
    if any(
        stat.S_ISLNK(parent.lstat().st_mode)
        or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400
        for parent in [path, *path.parents]
    ):
        raise PermissionError("OAuth configuration cannot follow links")
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("OAuth configuration exceeds 16 KiB")
    return OAuthResourceConfig.model_validate_json(raw)


class OAuthTokenVerifier:
    def __init__(self, config: OAuthResourceConfig, public_origin: str) -> None:
        self.config = config
        self.origin = checked_origin(public_origin)
        if not self.origin.startswith("https://"):
            raise ValueError("OAuth MCP requires an HTTPS public origin")
        self.resource = self.origin + "/mcp"
        self.metadata_url = self.origin + "/.well-known/oauth-protected-resource/mcp"
        self.verify = tls_context(config.ca_file) or True
        self.keys: dict[str, jwt.PyJWK] = {}
        self.deadline = 0.0
        self.next_refresh = 0.0
        self.lock = asyncio.Lock()

    async def refresh(self) -> None:
        async with self.lock:
            current = time.monotonic()
            if current < self.next_refresh:
                return
            self.next_refresh = current + 5
            async with httpx.AsyncClient(
                verify=self.verify, follow_redirects=False, trust_env=False, timeout=5
            ) as http:
                async with http.stream("GET", self.config.jwks_uri) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 256 * 1024:
                            raise ValueError("OAuth signing key response exceeds limit")
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError("invalid OAuth signing key document")
            entries = document.get("keys")
            if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
                raise ValueError("OAuth signing key inventory exceeds limit")
            keys: dict[str, jwt.PyJWK] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("invalid OAuth signing key")
                kid = entry.get("kid")
                if not isinstance(kid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", kid):
                    raise ValueError("invalid signing key identifier")
                if kid in keys or any(name in entry for name in ["d", "p", "q", "oth"]):
                    raise ValueError("duplicate or private OAuth key")
                if entry.get("use", "sig") != "sig":
                    continue
                if "key_ops" in entry and entry["key_ops"] != ["verify"]:
                    continue
                key = jwt.PyJWK.from_dict(entry)
                if key.algorithm_name not in self.config.algorithms:
                    continue
                if key.algorithm_name == "ES256" and entry.get("crv") != "P-256":
                    raise ValueError("unsupported OAuth EC curve")
                if key.key_type == "RSA" and getattr(key.key, "key_size", 0) < 2048:
                    raise ValueError("OAuth RSA key is too small")
                keys[kid] = key
            if not keys:
                raise ValueError("OAuth provider has no approved signing keys")
            self.keys, self.deadline = keys, time.monotonic() + self.config.jwks_ttl_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            if len(token) > 16384 or token.count(".") != 2:
                return None
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.config.algorithms or header.get("typ", "JWT") not in {
                "JWT",
                "at+jwt",
            }:
                return None
            kid = header.get("kid")
            if not isinstance(kid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", kid):
                return None
            if time.monotonic() >= self.deadline or kid not in self.keys:
                await self.refresh()
            if time.monotonic() >= self.deadline or kid not in self.keys:
                return None
            key = self.keys[kid]
            if header["alg"] != key.algorithm_name:
                return None
            claims = jwt.decode(
                token,
                key.key,
                algorithms=self.config.algorithms,
                issuer=self.config.issuer,
                audience=self.resource,
                options={"require": ["iss", "aud", "sub", "exp", "iat"]},
            )
            expiry, issued = claims["exp"], claims["iat"]
            if (
                type(expiry) is not int
                or type(issued) is not int
                or not 0 < expiry - issued <= self.config.max_token_seconds
                or "nbf" in claims
                and type(claims["nbf"]) is not int
                or claims["sub"] != self.config.owner_subject
            ):
                return None
            client = claims.get("azp", claims.get("client_id"))
            if client not in self.config.client_ids:
                return None
            scopes = claims.get("scope", "")
            if not isinstance(scopes, str) or len(scopes) > 4096:
                return None
            return AccessToken(
                token=token,
                client_id=client,
                subject=claims["sub"],
                scopes=scopes.split(),
                expires_at=expiry,
                resource=self.resource,
                claims={"iss": self.config.issuer},
            )
        except (
            jwt.PyJWTError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
            RecursionError,
        ):
            return None

    def metadata(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "authorization_servers": [self.config.issuer],
            "scopes_supported": [self.config.read_scope, self.config.execute_scope],
            "bearer_methods_supported": ["header"],
            "resource_name": "RACP Remote PC",
        }


class OAuthToolScopes:
    def __init__(self, verifier: OAuthTokenVerifier) -> None:
        self.verifier = verifier
        self.mutations = (
            {spec.mcp_name for spec in REGISTRY.values() if spec.side_effect}
            | {
                "operation_cancel",
                "job_cancel",
            }
            | {REGISTRY[name].mcp_name for name in SENSITIVE_PROCESS_READS}
        )

    def schemes(self, name: str) -> list[dict[str, Any]]:
        config = self.verifier.config
        return [
            {
                "type": "oauth2",
                "scopes": [config.read_scope, config.execute_scope]
                if name in self.mutations
                else [config.read_scope],
            }
        ]

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method == "tools/call":
            name = (ctx.params or {}).get("name")
            access = get_access_token()
            if (
                isinstance(name, str)
                and name in self.mutations
                and (access is None or self.verifier.config.execute_scope not in access.scopes)
            ):
                challenge = (
                    f'Bearer resource_metadata="{self.verifier.metadata_url}", '
                    'error="insufficient_scope", '
                    'error_description="Remote operation permission required", '
                    f'scope="{self.verifier.config.read_scope} '
                    f'{self.verifier.config.execute_scope}"'
                )
                return CallToolResult(
                    result_type="complete",
                    is_error=True,
                    content=[TextContent(type="text", text="OAuth execution scope required")],
                    structured_content={
                        "error": {
                            "code": "PERMISSION_DENIED",
                            "message": "OAuth execution scope required",
                        }
                    },
                    _meta={"mcp/www_authenticate": [challenge]},
                )
        result = await call_next(ctx)
        if ctx.method == "tools/list":
            payload = (
                result.model_dump(by_alias=True) if isinstance(result, ListToolsResult) else result
            )
            if isinstance(payload, dict):
                for tool in payload.get("tools", []):
                    schemes = self.schemes(tool["name"])
                    tool["_meta"] = {**tool.get("_meta", {}), "securitySchemes": schemes}
                    tool["securitySchemes"] = schemes
                return payload
        return result
