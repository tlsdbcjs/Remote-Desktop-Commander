import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from racp_gateway.app import create_app
from racp_gateway.oauth import OAuthResourceConfig, OAuthTokenVerifier, OAuthToolScopes


@pytest.fixture
def signed() -> tuple[OAuthTokenVerifier, Any, dict[str, Any]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="approved", use="sig", alg="RS256")
    verifier = OAuthTokenVerifier(
        OAuthResourceConfig(
            issuer="https://idp.example/realm",
            jwks_uri="https://idp.example/keys",
            owner_subject="owner-test",
            client_ids=["allowed-client"],
        ),
        "https://gateway.example",
    )
    verifier.keys = {"approved": jwt.PyJWK.from_dict(jwk)}
    verifier.deadline = time.monotonic() + 300
    return verifier, private, jwk


@pytest.mark.parametrize("name", ["process_memory_regions", "process_memory_read"])
async def test_memory_tools_reject_read_only_oauth_before_dispatch(
    signed: Any, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    import racp_gateway.oauth as oauth

    verifier, _, _ = signed
    middleware = OAuthToolScopes(verifier)
    scopes = [verifier.config.read_scope]
    monkeypatch.setattr(oauth, "get_access_token", lambda: SimpleNamespace(scopes=scopes))
    context = SimpleNamespace(method="tools/call", params={"name": name})
    calls = []

    async def dispatch(ctx: Any) -> Any:
        calls.append(ctx)
        return "dispatched"

    denied = await middleware(context, dispatch)
    assert denied.is_error and denied.structured_content["error"]["code"] == "PERMISSION_DENIED"
    assert calls == []
    assert middleware.schemes(name)[0]["scopes"] == [
        verifier.config.read_scope,
        verifier.config.execute_scope,
    ]
    scopes.append(verifier.config.execute_scope)
    assert await middleware(context, dispatch) == "dispatched"
    assert calls == [context]


def claims() -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": "https://idp.example/realm",
        "aud": "https://gateway.example/mcp",
        "sub": "owner-test",
        "azp": "allowed-client",
        "iat": now,
        "exp": now + 300,
        "scope": "racp.read racp.execute",
    }


def encoded(private: Any, payload: dict[str, Any], **header: Any) -> str:
    return str(
        jwt.encode(payload, private, algorithm="RS256", headers={"kid": "approved", **header})
    )


async def test_verified_resource_identity_and_scope_are_preserved(signed: Any) -> None:
    verifier, private, _ = signed
    result = await verifier.verify_token(
        encoded(private, claims(), jku="https://attacker.invalid/keys")
    )
    assert (
        result is not None
        and result.subject == "owner-test"
        and result.resource == verifier.resource
    )
    assert result.scopes == ["racp.read", "racp.execute"] and result.claims == {
        "iss": verifier.config.issuer
    }
    assert await verifier.verify_token("device-owner-opaque-credential") is None


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://idp.example/realm/"},
        {"aud": "https://other.example/mcp"},
        {"sub": "different-owner"},
        {"azp": "unapproved-client"},
        {"exp": 1},
        {"nbf": 9999999999},
        {"iat": 9999999999},
        {"exp": 9999999999},
        {"exp": True},
        {"scope": ["racp.read"]},
    ],
)
async def test_wrong_issuer_resource_user_client_time_and_scope_are_denied(
    signed: Any, change: Any
) -> None:
    verifier, private, _ = signed
    assert await verifier.verify_token(encoded(private, {**claims(), **change})) is None


async def test_signature_algorithm_and_missing_audience_are_denied(signed: Any) -> None:
    verifier, private, _ = signed
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert await verifier.verify_token(encoded(other, claims())) is None
    mac = jwt.encode(
        claims(),
        "untrusted shared secret of sufficient length",
        algorithm="HS256",
        headers={"kid": "approved"},
    )
    assert await verifier.verify_token(mac) is None
    data = claims()
    del data["aud"]
    assert await verifier.verify_token(encoded(private, data)) is None
    nested = base64.urlsafe_b64encode(("[" * 2000 + "0" + "]" * 2000).encode()).decode()
    assert await verifier.verify_token(nested + ".e30.signature") is None


async def test_jwks_fetch_is_pinned_bounded_and_expired_cache_fails_closed(
    signed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, private, jwk = signed
    verifier.keys = {}
    verifier.deadline = 0
    urls = []
    failing = False

    def response(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(503) if failing else httpx.Response(200, json={"keys": [jwk]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
    )
    credential = encoded(private, claims(), jku="https://attacker.invalid/keys")
    assert await verifier.verify_token(credential) is not None
    assert urls == [verifier.config.jwks_uri]
    failing = True
    verifier.deadline = verifier.next_refresh = 0
    assert await verifier.verify_token(credential) is None


def test_oauth_configuration_cannot_disable_tls_or_signature_constraints(tmp_path: Path) -> None:
    for change in [
        {"issuer": "http://localhost/realm"},
        {"jwks_uri": "https://idp.example:bad/keys"},
        {"algorithms": ["HS256"]},
        {"ca_file": Path("relative.pem")},
        {"read_scope": "same", "execute_scope": "same"},
    ]:
        with pytest.raises((ValidationError, ValueError)):
            OAuthResourceConfig.model_validate(
                {
                    "issuer": "https://idp.example/realm",
                    "jwks_uri": "https://idp.example/keys",
                    "owner_subject": "owner-test",
                    "client_ids": ["allowed-client"],
                    **change,
                }
            )


@pytest.mark.parametrize("document", [[], {"keys": []}, {"keys": [{}]}, {"keys": "invalid"}])
async def test_malformed_provider_keys_fail_closed(
    signed: Any, monkeypatch: pytest.MonkeyPatch, document: Any
) -> None:
    verifier, private, _ = signed
    verifier.keys = {}
    verifier.deadline = 0
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=document)),
            **kwargs,
        ),
    )
    assert await verifier.verify_token(encoded(private, claims())) is None


@pytest.mark.parametrize(
    "origin,peer",
    [(None, "198.51.100.3"), ("https://gateway.example", "127.0.0.1"), (None, "not-an-ip")],
)
async def test_owner_bearer_mcp_is_confined_to_local_loopback(
    tmp_path: Path, origin: str | None, peer: str
) -> None:
    app = create_app(tmp_path, public_origin=origin)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(peer, 12345)),
            base_url=origin or "http://localhost",
        ) as http:
            response = await http.post("/mcp/", headers={"Authorization": "Bearer opaque"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    finally:
        app.state.control.store.close()
