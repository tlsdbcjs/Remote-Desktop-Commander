import asyncio
import hashlib
import sys
from typing import Any

import httpx2
from conftest import shell_request
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_domain.version import VERSION


async def test_agent_advertises_running_workspace_version(live: dict[str, Any]) -> None:
    response = await live["client"].get("/api/v1/devices/" + live["device_id"])
    assert response.status_code == 200
    info = response.json()["info"]
    assert info["status"] == "ONLINE"
    assert info["agent_version"] == VERSION


async def test_auth_01_device_token_cannot_control_owner_api(live: dict[str, Any]) -> None:
    client = live["client"]
    assert (await client.get("/healthz", headers={"Authorization": ""})).status_code == 200
    assert (await client.get("/readyz", headers={"Authorization": ""})).status_code == 401
    assert (
        await client.get(
            "/api/v1/devices", headers={"Authorization": "Bearer " + live["credential"]}
        )
    ).status_code == 401
    enrollment = (
        await client.post("/api/v1/enrollment-tokens", json={"name": "single-use"})
    ).json()
    first = await client.post("/agent/v1/enroll", json={"token": enrollment["token"]})
    second = await client.post("/agent/v1/enroll", json={"token": enrollment["token"]})
    assert first.status_code == 200 and second.status_code == 401
    assert (
        await client.get("/api/v1/devices", headers={"Origin": "https://attacker.test"})
    ).status_code == 403
    assert (await client.post("/mcp/", json={}, headers={"Authorization": ""})).status_code == 401


async def test_shell_01_unicode_env_and_separate_outputs(live: dict[str, Any]) -> None:
    code = (
        "import os,sys; print(sys.argv[1]); print(os.getcwd()); "
        "print(os.getenv('SAFE_TEST')); print('error',file=sys.stderr); sys.exit(7)"
    )
    request = shell_request(live, [sys.executable, "-c", code, "한글 공백 인자"])
    request["payload"]["env"] = {"RACP_TEST": None, "SAFE_TEST": "value"}
    response = await live["client"].post("/api/v1/operations", json=request)
    assert response.status_code == 403 or response.json().get("state") == "FAILED"
    request["idempotency_key"] = "unicode-command"
    request["payload"]["env"] = {"SAFE_TEST": None, "PYTHONIOENCODING": "utf-8"}
    response = await live["client"].post("/api/v1/operations", json=request)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert "한글 공백 인자" in result["stdout"]
    assert str(live["workspace"]) in result["stdout"]
    assert result["stderr"].strip() == "error" and result["exit_code"] == 7
    assert result["cleanup_status"] == "complete"


async def test_rpc_01_one_hundred_concurrent_mutations_execute_once(live: dict[str, Any]) -> None:
    code = (
        "from pathlib import Path; p=Path('counter'); "
        "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
    )
    request = shell_request(live, [sys.executable, "-c", code], "one-hundred")
    responses = await asyncio.gather(
        *[live["client"].post("/api/v1/operations", json=request) for _ in range(100)]
    )
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["operation_id"] for response in responses}) == 1
    assert (live["workspace"] / "counter").read_text() == "1"
    request["payload"]["argv"][-1] = "print('different')"
    response = await live["client"].post("/api/v1/operations", json=request)
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    )


async def test_policy_01_default_deny_approval_bound_and_one_time(live: dict[str, Any]) -> None:
    client = live["client"]
    request = shell_request(live, [sys.executable, "-c", "print('approved')"], "approval")
    request["execution_profile_id"] = "read_only"
    assert (await client.post("/api/v1/operations", json=request)).status_code == 403
    request["execution_profile_id"] = "standard"
    response = await client.post("/api/v1/operations", json=request)
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    request["approval_id"] = details["approval_id"]
    assert (
        await client.post("/api/v1/approvals/" + details["approval_id"] + "/approve")
    ).status_code == 200
    response = await client.post("/api/v1/operations", json=request)
    assert (
        response.status_code == 200 and response.json()["operation_id"] == details["operation_id"]
    )
    assert response.json()["result"]["stdout"].strip() == "approved"
    replay = await client.post("/api/v1/operations", json=request)
    assert replay.json() == response.json()
    request["idempotency_key"] = "new-execution"
    assert (await client.post("/api/v1/operations", json=request)).status_code == 403


async def test_mcp_http_to_agent_real_command(live: dict[str, Any]) -> None:
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            result = await client.call_tool(
                "shell_exec",
                {
                    "device_id": live["device_id"],
                    "argv": [sys.executable, "--version"],
                    "idempotency_key": "mcp-first",
                    "execution_profile_id": "trusted_personal",
                },
            )
            assert not result.is_error, result
            assert result.structured_content["result"]["exit_code"] == 0
            denied = await client.call_tool(
                "shell_exec",
                {
                    "device_id": live["device_id"],
                    "argv": [sys.executable, "--version"],
                    "idempotency_key": "mcp-denied",
                },
            )
            assert (
                denied.is_error
                and denied.structured_content["error"]["code"] == "PERMISSION_DENIED"
            )


async def test_shell_output_spills_to_scoped_hashed_artifact(live: dict[str, Any]) -> None:
    response = await live["client"].post(
        "/api/v1/operations", json=shell_request(live, [sys.executable, "-c", "print('x'*200000)"])
    )
    result = response.json()["result"]
    assert result["truncated"] and result["artifact_id"]
    artifact_id = result["artifact_id"]
    metadata = (await live["client"].get("/api/v1/artifacts/" + artifact_id)).json()
    content = await live["client"].get("/api/v1/artifacts/" + artifact_id + "/content")
    assert hashlib.sha256(content.content).hexdigest() == metadata["sha256"]
    assert (
        await live["client"].get(
            "/api/v1/artifacts/" + artifact_id + "/content",
            headers={"Authorization": "Bearer " + live["credential"]},
        )
    ).status_code == 401
