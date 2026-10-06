import ssl
from pathlib import Path

import httpx
import pytest
from racp_gateway.app import create_app
from racp_gateway.network import GatewayNetwork, checked_origin
from racp_sdk.security import tls_context
from tls_fixture import certificates


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.20", "gateway.example"])
def test_non_loopback_listener_cannot_start_without_tls_and_origin(host: str) -> None:
    with pytest.raises(ValueError, match="requires TLS"):
        GatewayNetwork(host=host).validate()


def test_valid_remote_tls_requires_certificate_pair_and_uses_exact_origin(tmp_path: Path) -> None:
    ca, cert, key = certificates(tmp_path / "tls")
    with pytest.raises(ValueError, match="together"):
        GatewayNetwork(certificate=cert).validate()
    network = GatewayNetwork(
        host="0.0.0.0",
        public_origin="https://gateway.example:8765",
        certificate=cert,
        private_key=key,
    )
    options = network.uvicorn_options()
    assert options["proxy_headers"] is False and options["ssl_certfile"] == str(cert)
    context = tls_context(ca)
    assert (
        context is not None and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    )
    assert checked_origin("https://GATEWAY.EXAMPLE:443/") == "https://gateway.example"
    with pytest.raises(ValueError):
        checked_origin("https://gateway.example/mcp/")


async def test_forwarded_headers_cannot_turn_plain_ingress_into_https(tmp_path: Path) -> None:
    app = create_app(tmp_path, public_origin="https://gateway.example")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.example"
    ) as client:
        response = await client.get(
            "/readyz", headers={"X-Forwarded-Proto": "https", "Forwarded": "proto=https"}
        )
        assert response.status_code == 403
    app.state.control.store.close()
