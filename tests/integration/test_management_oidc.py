import json
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from racp_domain.models import RACPError
from racp_gateway.console_auth import ConsoleAuth
from racp_gateway.management.oidc import OidcLogin, OidcManagementConfig
from racp_gateway.management.users import UserManagement
from racp_gateway.store import GatewayStore
from racp_protocol.management import ManagementPrincipal
from racp_sdk.security import digest


def owner() -> ManagementPrincipal:
    return ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)


@pytest.fixture
def oidc(tmp_path: Path) -> tuple[OidcLogin, GatewayStore, Any, dict[str, Any]]:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    users = UserManagement(store)
    users.create(
        owner(),
        issuer="https://idp.example/realm",
        subject="registered-user",
        display_name="Registered",
        role="operator",
    )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="management-key", use="sig", alg="RS256")
    login = OidcLogin(
        OidcManagementConfig(
            issuer="https://idp.example/realm",
            authorization_endpoint="https://idp.example/authorize",
            token_endpoint="https://idp.example/token",
            jwks_uri="https://idp.example/keys",
            client_id="racp-management",
            redirect_uri="https://gateway.example/console/oidc/callback",
        ),
        store,
        users,
        ConsoleAuth(store),
    )
    return login, store, private, jwk


def signed(
    private: Any,
    nonce: str,
    *,
    subject: str = "registered-user",
    issuer: str = "https://idp.example/realm",
) -> str:
    now = int(time.time())
    return str(
        jwt.encode(
            {
                "iss": issuer,
                "aud": "racp-management",
                "sub": subject,
                "iat": now,
                "exp": now + 300,
                "nonce": nonce,
            },
            private,
            algorithm="RS256",
            headers={"kid": "management-key"},
        )
    )


async def test_oidc_validates_state_nonce_issuer_and_registered_subject(
    oidc: tuple[OidcLogin, GatewayStore, Any, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    login, store, private, jwk = oidc
    begin = login.begin()
    state = store.db.execute("SELECT nonce FROM oidc_login_state").fetchone()
    assert state is not None
    id_token = signed(private, state["nonce"])

    def response(request: httpx.Request) -> httpx.Response:
        if str(request.url) == login.config.token_endpoint:
            return httpx.Response(200, json={"id_token": id_token})
        if str(request.url) == login.config.jwks_uri:
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
    )
    receipt = await login.complete("authorization-code", begin.state)
    assert receipt.principal.role == "operator"
    assert receipt.credential
    with pytest.raises(RACPError):
        await login.complete("authorization-code", begin.state)
    store.close()


@pytest.mark.parametrize("kind", ["nonce", "issuer", "subject"])
async def test_oidc_wrong_claims_or_unregistered_subject_fail_closed(
    oidc: tuple[OidcLogin, GatewayStore, Any, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    login, store, private, jwk = oidc
    begin = login.begin()
    row = store.db.execute("SELECT nonce FROM oidc_login_state WHERE used=0").fetchone()
    assert row is not None
    nonce = "wrong" if kind == "nonce" else row["nonce"]
    issuer = "https://attacker.example" if kind == "issuer" else login.config.issuer
    subject = "unknown-user" if kind == "subject" else "registered-user"
    id_token = signed(private, nonce, issuer=issuer, subject=subject)

    def response(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json={"id_token": id_token})
            if str(request.url) == login.config.token_endpoint
            else httpx.Response(200, json={"keys": [jwk]})
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
    )
    with pytest.raises(RACPError) as denied:
        await login.complete("authorization-code", begin.state)
    assert denied.value.error.code in {"UNAUTHENTICATED", "PERMISSION_DENIED"}
    store.close()
