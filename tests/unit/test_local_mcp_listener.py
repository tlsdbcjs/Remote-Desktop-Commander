"""Local OAuth ingress shares the Agent control plane without exposing owner routes."""

from pathlib import Path

import httpx
import pytest
from racp_gateway.app import create_app
from racp_gateway.oauth import OAuthResourceConfig


def provider() -> OAuthResourceConfig:
    return OAuthResourceConfig(
        issuer="https://127.0.0.1:19443/realm",
        jwks_uri="https://127.0.0.1:19443/keys",
        owner_subject="test-owner",
        client_ids=["test-client"],
    )


@pytest.mark.parametrize(
    "origin,path,peer,server,expected",
    [
        (
            "https://127.0.0.1:18765",
            "/.well-known/oauth-protected-resource/mcp",
            "127.0.0.1",
            ("127.0.0.1", 18765),
            200,
        ),
        ("https://127.0.0.1:18765", "/mcp/", "127.0.0.1", ("127.0.0.1", 18765), 401),
        ("https://127.0.0.1:18765", "/api/v1/devices", "127.0.0.1", ("127.0.0.1", 18765), 403),
        ("https://127.0.0.1:18765", "/mcp/", "192.0.2.141", ("192.0.2.140", 8765), 403),
        ("https://127.0.0.1:18765", "/mcp/", "127.0.0.1", ("192.0.2.140", 8765), 403),
        ("https://127.0.0.1:18765", "/mcp/", "127.0.0.1", ("127.0.0.1", 8765), 403),
        ("https://gateway.example:8765", "/mcp/", "192.0.2.141", ("192.0.2.140", 8765), 403),
        (
            "https://gateway.example:8765",
            "/api/v1/devices",
            "127.0.0.1",
            ("127.0.0.1", 18765),
            403,
        ),
        (
            "https://gateway.example:8765",
            "/api/v1/devices",
            "192.0.2.141",
            ("192.0.2.140", 8765),
            401,
        ),
    ],
)
async def test_origin_peer_socket_and_route_are_checked(
    tmp_path: Path, origin: str, path: str, peer: str, server: tuple[str, int], expected: int
) -> None:
    app = create_app(
        tmp_path,
        public_origin="https://gateway.example:8765",
        oauth_config=provider(),
        local_mcp_port=18765,
    )

    async def transport(scope, receive, send):
        scope["server"] = server
        await app(scope, receive, send)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport, client=(peer, 12345)), base_url=origin
        ) as http:
            response = await http.get(path, headers={"X-Forwarded-For": "127.0.0.1"})
            assert response.status_code == expected
            if expected == 200:
                assert response.json()["resource"] == "https://127.0.0.1:18765/mcp"
            if expected == 401 and path.startswith("/mcp"):
                assert (
                    "https://127.0.0.1:18765/.well-known/oauth-protected-resource/mcp"
                    in response.headers["www-authenticate"]
                )
    finally:
        app.state.control.store.close()


def test_local_ingress_cannot_disable_tls_or_oauth(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="configured OAuth"):
        create_app(tmp_path, public_origin="https://gateway.example", local_mcp_port=18765)
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(
            tmp_path,
            public_origin="http://localhost",
            oauth_config=provider(),
            local_mcp_port=18765,
        )
