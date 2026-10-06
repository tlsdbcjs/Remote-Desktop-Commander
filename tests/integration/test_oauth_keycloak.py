"""Opt-in real Keycloak authorization-code/PKCE consent and HTTPS MCP workflow."""

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_agent.runtime import Agent
from racp_gateway.oauth import OAuthResourceConfig
from racp_gateway.store import GatewayStore
from racp_sdk.security import digest, tls_context, token
from tls_fixture import certificates

pytestmark = pytest.mark.oauth_keycloak
IMAGE = (
    "quay.io/keycloak/keycloak@sha256:"
    "b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc"
)


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def realm(resource: str, callback: str, subject: str, password: str) -> dict[str, Any]:
    return {
        "realm": "racp-test",
        "enabled": True,
        "sslRequired": "all",
        "registrationAllowed": False,
        "accessTokenLifespan": 120,
        "users": [
            {
                "id": subject,
                "username": "racp-test",
                "enabled": True,
                "firstName": "RACP",
                "lastName": "Fixture",
                "email": "fixture@example.invalid",
                "emailVerified": True,
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            }
        ],
        "clientScopes": [
            {
                "name": "basic",
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "false"},
                "protocolMappers": [
                    {
                        "name": "sub",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-sub-mapper",
                        "config": {"access.token.claim": "true"},
                    }
                ],
            },
        ]
        + [
            {
                "name": scope,
                "protocol": "openid-connect",
                "attributes": {
                    "include.in.token.scope": "true",
                    "display.on.consent.screen": "true",
                    "consent.screen.text": description,
                },
            }
            for scope, description in [
                ("racp.read", "Read remote PC files"),
                ("racp.execute", "Run remote PC operations"),
            ]
        ],
        "clients": [
            {
                "clientId": "racp-test-client",
                "name": "RACP fixture",
                "enabled": True,
                "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "consentRequired": True,
                "redirectUris": [callback],
                "defaultClientScopes": ["basic", "racp.read"],
                "optionalClientScopes": ["racp.execute"],
                "attributes": {"pkce.code.challenge.method": "S256"},
                "protocolMappers": [
                    {
                        "name": "RACP audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.custom.audience": resource,
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                            "userinfo.token.claim": "false",
                        },
                    }
                ],
            }
        ],
    }


async def command(*arguments: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output, _ = await process.communicate()
    return process.returncode or 0, output.decode(errors="replace")


async def authorize(
    http: httpx.AsyncClient, issuer: str, callback: str, resource: str, password: str, scopes: str
) -> dict[str, Any]:
    verifier = token()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    state = token()
    response = await http.get(
        issuer + "/protocol/openid-connect/auth",
        params={
            "client_id": "racp-test-client",
            "redirect_uri": callback,
            "response_type": "code",
            "scope": scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
            "prompt": "login consent",
        },
    )
    assert response.status_code == 200
    action = urljoin(
        str(response.url), html.unescape(re.search(r'<form[^>]+action="([^"]+)"', response.text)[1])
    )
    response = await http.post(
        action, data={"username": "racp-test", "password": password, "credentialId": ""}
    )
    for _ in range(6):
        if response.status_code in {302, 303}:
            location = response.headers["location"]
            if location.startswith(callback):
                values = parse_qs(urlsplit(location).query)
                assert values["state"] == [state] and values["iss"] == [issuer]
                code = values["code"][0]
                result = await http.post(
                    issuer + "/protocol/openid-connect/token",
                    data={
                        "grant_type": "authorization_code",
                        "client_id": "racp-test-client",
                        "redirect_uri": callback,
                        "code": code,
                        "code_verifier": verifier,
                        "resource": resource,
                    },
                )
                assert result.status_code == 200
                replay = await http.post(
                    issuer + "/protocol/openid-connect/token",
                    data={
                        "grant_type": "authorization_code",
                        "client_id": "racp-test-client",
                        "redirect_uri": callback,
                        "code": code,
                        "code_verifier": verifier,
                        "resource": resource,
                    },
                )
                assert replay.status_code == 400
                return result.json()
            response = await http.get(location)
        else:
            assert response.status_code == 200, response.text[:1000]
            action = urljoin(
                str(response.url),
                html.unescape(re.search(r'<form[^>]+action="([^"]+)"', response.text)[1]),
            )
            response = await http.post(action, data={"accept": "Yes"})
    raise AssertionError("provider consent did not return an authorization code")


async def test_real_keycloak_pkce_consent_scope_and_mcp_remote_file_shell(tmp_path: Path) -> None:
    if os.environ.get("RACP_TEST_KEYCLOAK") != "1":
        pytest.skip("requires explicit local Docker Keycloak integration opt-in")
    ca, certificate, key = certificates(tmp_path / "tls")
    identity = str(uuid.uuid4())
    password = token()
    gateway = f"https://localhost:{port()}"
    issuer = f"https://localhost:{port()}/realms/racp-test"
    callback = gateway + "/fixture-callback"
    resource = gateway + "/mcp"
    imported = tmp_path / "tls/import"
    imported.mkdir()
    (imported / "realm.json").write_text(
        json.dumps(realm(resource, callback, identity, password)), encoding="utf-8"
    )
    name = "racp-keycloak-" + uuid.uuid4().hex
    code, output = await command(
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        "racp.test=" + name,
        "--memory",
        "1g",
        "--cpus",
        "2",
        "--pids-limit",
        "256",
        "--publish",
        "127.0.0.1:" + str(urlsplit(issuer).port) + ":8443",
        "--mount",
        f"type=bind,src={tmp_path / 'tls'},dst=/racp,readonly",
        "--mount",
        f"type=bind,src={imported},dst=/opt/keycloak/data/import,readonly",
        IMAGE,
        "start-dev",
        "--http-enabled=false",
        "--https-certificate-file=/racp/server.pem",
        "--https-certificate-key-file=/racp/server.key",
        "--hostname=" + issuer.split("/realms/")[0],
        "--import-realm",
    )
    assert code == 0, output
    process = None
    agent = None
    agent_task = None
    gateway_log = (tmp_path / "gateway.log").open("wb")
    try:
        async with httpx.AsyncClient(
            verify=tls_context(ca), follow_redirects=False, timeout=10, trust_env=False
        ) as http:
            for _ in range(240):
                try:
                    discovery = await http.get(issuer + "/.well-known/openid-configuration")
                    if discovery.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.5)
            else:
                _, logs = await command("docker", "logs", name)
                pytest.fail(logs[-12000:])
            metadata = discovery.json()
            assert (
                metadata["issuer"] == issuer
                and "S256" in metadata["code_challenge_methods_supported"]
            )
            assert metadata["authorization_response_iss_parameter_supported"]
            config = OAuthResourceConfig(
                issuer=issuer,
                jwks_uri=metadata["jwks_uri"],
                owner_subject=identity,
                client_ids=["racp-test-client"],
                ca_file=ca,
            )
            config_file = tmp_path / "oauth.json"
            config_file.write_text(config.model_dump_json())
            data = tmp_path / "gateway"
            store = GatewayStore(data / "gateway.db")
            owner = token()
            store.initialize(digest(owner))
            enrollment = store.enroll(store.enrollment("OAuth PC"))
            store.close()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "racp_gateway.main",
                "--data-dir",
                str(data),
                "--port",
                str(urlsplit(gateway).port),
                "--public-origin",
                gateway,
                "--tls-cert",
                str(certificate),
                "--tls-key",
                str(key),
                "--oauth-config",
                str(config_file),
                "--enable-trusted-personal",
                stdout=gateway_log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for _ in range(200):
                try:
                    if (
                        await http.get(
                            gateway + "/readyz", headers={"Authorization": "Bearer " + owner}
                        )
                    ).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            else:
                pytest.fail((tmp_path / "gateway.log").read_text(errors="replace")[-2000:])
            unauthorized = await http.post(gateway + "/mcp/")
            assert (
                unauthorized.status_code == 401
                and "resource_metadata=" in unauthorized.headers["www-authenticate"]
            )
            protected = (
                await http.get(gateway + "/.well-known/oauth-protected-resource/mcp")
            ).json()
            assert protected["resource"] == resource and protected["authorization_servers"] == [
                issuer
            ]
            workspace = tmp_path / "remote-workspace"
            workspace.mkdir()
            agent = Agent(
                gateway,
                enrollment["credential"],
                enrollment["device_id"],
                workspace,
                tmp_path / "agent",
                profile="trusted_personal",
                ca_file=ca,
            )
            agent_task = asyncio.create_task(agent.run())
            for _ in range(200):
                devices = (
                    await http.get(
                        gateway + "/api/v1/devices", headers={"Authorization": "Bearer " + owner}
                    )
                ).json()
                if devices["items"] and devices["items"][0]["info"].get("status") == "ONLINE":
                    break
                await asyncio.sleep(0.05)
            readonly = await authorize(http, issuer, callback, resource, password, "racp.read")
            async with httpx2.AsyncClient(
                verify=tls_context(ca),
                headers={"Authorization": "Bearer " + readonly["access_token"]},
            ) as transport:
                async with Client(
                    streamable_http_client(gateway + "/mcp/", http_client=transport)
                ) as client:
                    result = await client.call_tool("device_list", {})
                    assert not result.is_error
                    denied = await client.call_tool(
                        "shell_exec",
                        {
                            "device_id": enrollment["device_id"],
                            "argv": [sys.executable, "--version"],
                            "idempotency_key": "oauth-denied",
                            "execution_profile_id": "trusted_personal",
                        },
                    )
                    assert (
                        denied.is_error
                        and denied.structured_content["error"]["code"] == "PERMISSION_DENIED"
                    )
                    assert "mcp/www_authenticate" in denied.meta
                    descriptors = await client.list_tools()
                    assert all(
                        tool.meta and "securitySchemes" in tool.meta for tool in descriptors.tools
                    )
            runnable = await authorize(
                http, issuer, callback, resource, password, "racp.read racp.execute"
            )
            async with httpx2.AsyncClient(
                verify=tls_context(ca),
                headers={"Authorization": "Bearer " + runnable["access_token"]},
            ) as transport:
                async with Client(
                    streamable_http_client(gateway + "/mcp/", http_client=transport)
                ) as client:
                    written = await client.call_tool(
                        "fs_write",
                        {
                            "device_id": enrollment["device_id"],
                            "path": "oauth-note.txt",
                            "content": "OAuth remote PC\n",
                            "idempotency_key": "oauth-write",
                            "execution_profile_id": "trusted_personal",
                        },
                    )
                    assert not written.is_error, written
                    executed = await client.call_tool(
                        "shell_exec",
                        {
                            "device_id": enrollment["device_id"],
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; "
                                "print(Path('oauth-note.txt').read_text())",
                            ],
                            "idempotency_key": "oauth-execute",
                            "execution_profile_id": "trusted_personal",
                        },
                    )
                    assert (
                        not executed.is_error
                        and "OAuth remote PC" in executed.structured_content["result"]["stdout"]
                    )
            assert (
                await http.get(
                    gateway + "/api/v1/devices",
                    headers={"Authorization": "Bearer " + runnable["access_token"]},
                )
            ).status_code == 401
            for denied_token in [owner, enrollment["credential"]]:
                assert (
                    await http.post(
                        gateway + "/mcp/", headers={"Authorization": "Bearer " + denied_token}
                    )
                ).status_code == 401
    finally:
        if agent_task is not None and agent is not None:
            agent.stopping.set()
            agent_task.cancel()
            await asyncio.gather(agent_task, return_exceptions=True)
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
        gateway_log.close()
        code, label = await command(
            "docker", "inspect", name, "--format", '{{index .Config.Labels "racp.test"}}'
        )
        if code == 0 and label.strip() == name:
            await command("docker", "rm", "--force", name)
