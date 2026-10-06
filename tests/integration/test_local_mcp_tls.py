"""Two real TLS sockets share one authenticated Agent control plane."""

import asyncio
import json
import socket
import time
from pathlib import Path

import httpx
import httpx2
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_agent.runtime import Agent
from racp_gateway.app import create_app
from racp_gateway.oauth import OAuthResourceConfig
from racp_gateway.store import GatewayStore
from racp_sdk.security import digest, tls_context, token
from tls_fixture import certificates

pytestmark = pytest.mark.remote_tls


async def test_local_oauth_mcp_reads_agent_on_other_tls_socket(tmp_path: Path) -> None:
    listeners = [socket.socket(), socket.socket()]
    server_task = agent_task = None
    server = agent = app = None
    try:
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
        lan_port, mcp_port = [listener.getsockname()[1] for listener in listeners]
        gateway = f"https://localhost:{lan_port}"
        resource = f"https://127.0.0.1:{mcp_port}/mcp"
        ca, certificate, private_key = certificates(tmp_path / "tls")
        data = tmp_path / "gateway"
        store = GatewayStore(data / "gateway.db")
        owner = token()
        store.initialize(digest(owner))
        device = store.enroll(store.enrollment("dual listener Agent"))
        store.close()
        provider = OAuthResourceConfig(
            issuer="https://idp.example/realm",
            jwks_uri="https://idp.example/keys",
            owner_subject="test-owner",
            client_ids=["test-client"],
        )
        app = create_app(
            data, public_origin=gateway, oauth_config=provider, local_mcp_port=mcp_port
        )
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
        jwk.update(kid="fixture", alg="RS256", use="sig")
        # Pin a fixture public key, preserving the production signature/claim checks.
        app.state.oauth.keys = {"fixture": jwt.PyJWK.from_dict(jwk)}
        app.state.oauth.deadline = time.monotonic() + 300
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                ssl_certfile=str(certificate),
                ssl_keyfile=str(private_key),
                proxy_headers=False,
                log_level="error",
                lifespan="on",
            )
        )
        server_task = asyncio.create_task(server.serve(sockets=listeners))
        for _ in range(300):
            if server.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.05)
        assert server.started
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        content = "별도 Agent 접점의 자료\n"
        (workspace / "자료.txt").write_bytes(content.encode("utf-8"))
        agent = Agent(
            gateway,
            device["credential"],
            device["device_id"],
            workspace,
            tmp_path / "agent",
            ca_file=ca,
            profile="read_only",
        )
        agent_task = asyncio.create_task(agent.run())
        async with httpx.AsyncClient(
            verify=tls_context(ca),
            trust_env=False,
            headers={"Authorization": "Bearer " + owner},
        ) as http:
            for _ in range(300):
                current = await http.get(gateway + "/api/v1/devices/" + device["device_id"])
                if current.json()["info"].get("status") == "ONLINE":
                    break
                if agent_task.done():
                    await agent_task
                await asyncio.sleep(0.05)
            assert current.json()["info"]["status"] == "ONLINE"
            now = int(time.time())
            claims = dict(
                iss=provider.issuer,
                sub=provider.owner_subject,
                azp="test-client",
                aud=resource,
                iat=now,
                exp=now + 120,
                scope="racp.read",
            )
            credential = jwt.encode(
                claims, signing_key, algorithm="RS256", headers={"kid": "fixture"}
            )
            wrong_audience = jwt.encode(
                {**claims, "aud": gateway + "/mcp"},
                signing_key,
                algorithm="RS256",
                headers={"kid": "fixture"},
            )
            assert (
                await http.get(
                    resource + "/", headers={"Authorization": "Bearer " + wrong_audience}
                )
            ).status_code == 401
        async with httpx2.AsyncClient(
            verify=tls_context(ca),
            trust_env=False,
            headers={"Authorization": "Bearer " + credential},
        ) as http:
            async with Client(streamable_http_client(resource + "/", http_client=http)) as client:
                devices = await client.call_tool("device_list", {})
                assert device["device_id"] in str(devices.structured_content)
                result = await client.call_tool(
                    "fs_read", {"device_id": device["device_id"], "path": "자료.txt"}
                )
                assert not result.is_error
                assert result.structured_content["result"]["text"] == content
    finally:
        if agent_task is not None:
            agent.stopping.set()
            agent_task.cancel()
            await asyncio.gather(agent_task, return_exceptions=True)
        if server_task is not None:
            server.should_exit = True
            await asyncio.wait_for(server_task, 10)
        elif app is not None:
            app.state.control.store.close()
        for listener in listeners:
            listener.close()
