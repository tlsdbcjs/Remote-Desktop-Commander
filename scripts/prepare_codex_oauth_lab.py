"""Prepare loopback OAuth for a Gateway started with --local-mcp-port 18765."""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from racp_gateway.oauth import OAuthResourceConfig
from racp_sdk.security import SecretStore, tls_context, token


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    lab = repository / ".racp/two-pc-lab"
    root = Path(os.environ["LOCALAPPDATA"]) / "RACP/codex-oauth-lab"
    root.mkdir(parents=True, exist_ok=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 19443))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 54881))
    sys.path.insert(0, str(repository / "tests"))
    specification = importlib.util.spec_from_file_location(
        "racp_oauth_fixture", repository / "tests/integration/test_oauth_keycloak.py"
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("OAuth fixture unavailable")
    fixture = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(fixture)
    subject, password = str(uuid.uuid4()), token()
    issuer = "https://127.0.0.1:19443/realms/racp-codex"
    callback = "http://127.0.0.1:54881/callback"
    resource = "https://127.0.0.1:18765/mcp"
    name = "racp-codex-oauth-" + uuid.uuid4().hex[:12]
    document = fixture.realm(resource, callback, subject, password)
    document["realm"] = "racp-codex"
    document["accessTokenLifespan"] = 900
    document["users"][0]["username"] = "racp-codex"
    document["clients"][0]["clientId"] = "racp-codex"
    document["clients"][0]["name"] = "RACP Codex local acceptance"
    document["clients"][0]["optionalClientScopes"].append("offline_access")
    document["clientScopes"].append(
        {
            "name": "offline_access",
            "protocol": "openid-connect",
            "attributes": {
                "include.in.token.scope": "true",
                "display.on.consent.screen": "true",
                "consent.screen.text": "Keep this owned RACP test connection available",
            },
        }
    )
    document["roles"] = {"realm": [{"name": "offline_access"}]}
    document["users"][0]["realmRoles"] = ["offline_access"]
    document["scopeMappings"] = [{"clientScope": "offline_access", "roles": ["offline_access"]}]
    imported = root / "import"
    imported.mkdir()
    realm_file = imported / "realm.json"
    realm_file.write_text(json.dumps(document), encoding="utf-8")
    tls = root / "tls"
    tls.mkdir()
    for file in ("server.pem", "server.key"):
        shutil.copy2(lab / file, tls / file)
    SecretStore(root / "account.bin").save(
        {
            "username": "racp-codex",
            "password": password,
            "subject": subject,
            "issuer": issuer,
            "callback": callback,
            "container": name,
        },
        overwrite=False,
    )
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--pull=never",
            "--label",
            "racp.codex-lab=" + name,
            "--memory",
            "1g",
            "--cpus",
            "2",
            "--pids-limit",
            "256",
            "--publish",
            "127.0.0.1:19443:8443",
            "--mount",
            f"type=bind,src={tls},dst=/racp,readonly",
            "--mount",
            f"type=bind,src={imported},dst=/opt/keycloak/data/import,readonly",
            fixture.IMAGE,
            "start-dev",
            "--http-enabled=false",
            "--https-certificate-file=/racp/server.pem",
            "--https-certificate-key-file=/racp/server.key",
            "--hostname=https://127.0.0.1:19443",
            "--import-realm",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("Owned OAuth container could not start")
    with httpx.Client(verify=tls_context(lab / "ca.pem"), trust_env=False, timeout=4) as http:
        deadline = time.monotonic() + 150
        while True:
            try:
                response = http.get(issuer + "/.well-known/openid-configuration")
                if response.status_code == 200:
                    metadata = response.json()
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned OAuth provider did not become ready")
            time.sleep(0.5)
    if metadata["issuer"] != issuer or "S256" not in metadata["code_challenge_methods_supported"]:
        raise RuntimeError("OAuth discovery differs from the configured provider")
    config = OAuthResourceConfig(
        issuer=issuer,
        jwks_uri=metadata["jwks_uri"],
        owner_subject=subject,
        client_ids=["racp-codex"],
        ca_file=(lab / "ca.pem").absolute(),
    )
    (root / "oauth.json").write_text(config.model_dump_json(), encoding="utf-8")
    realm_file.unlink()
    print(
        json.dumps(
            {
                "root": str(root),
                "issuer": issuer,
                "callback": callback,
                "resource": resource,
                "container": name,
                "ready": True,
            }
        )
    )


if __name__ == "__main__":
    main()
