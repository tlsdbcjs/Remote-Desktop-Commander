import asyncio
import json
import subprocess
import sys
import uuid
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_protocol.registry import validate_payload
from racp_sdk.artifacts import ArtifactClient
from racp_sdk.security import SecretStore, canonical_digest

pytestmark = pytest.mark.workspaces


async def remote(live: dict[str, Any], name: str, payload: dict[str, Any], **extra: Any) -> Any:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": name,
            "payload": payload,
            "execution_profile_id": "trusted_personal",
            "idempotency_key": uuid.uuid4().hex,
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "SUCCEEDED", result
    return result["result"]


async def test_mcp_search_edit_copy_move_execute_and_restart(live: dict[str, Any]) -> None:
    roots = live["workspaces"]
    info = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()["info"]
    filesystem = next(c for c in info["capabilities"] if c["name"] == "filesystem")
    assert filesystem["attributes"]["workspaces"] == [
        {"id": key, "path": str(path.resolve())} for key, path in roots.items()
    ]
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:

            async def call(name: str, **payload: Any) -> Any:
                reply = await client.call_tool(
                    name,
                    {
                        "device_id": live["device_id"],
                        "execution_profile_id": "trusted_personal",
                        "idempotency_key": uuid.uuid4().hex,
                        **payload,
                    },
                )
                assert not reply.is_error, reply
                return reply.structured_content["result"]

            await call("fs_write", workspace_id="docs", path="자료.txt", content="원격 자료\n")
            assert not (roots["default"] / "자료.txt").exists()
            found = await call("fs_search", workspace_id="docs", path=".", pattern="자료")
            assert str((roots["docs"] / "자료.txt").resolve()) in found["items"]
            await call(
                "fs_copy",
                workspace_id="docs",
                source="자료.txt",
                destination="copy.txt",
                destination_workspace_id="default",
            )
            await call(
                "fs_move",
                source="copy.txt",
                destination="moved.txt",
                destination_workspace_id="docs",
            )
            result = await call(
                "shell_exec",
                workspace_id="docs",
                argv=[
                    sys.executable,
                    "-X",
                    "utf8",
                    "-c",
                    "from pathlib import Path; "
                    "print(Path('moved.txt').read_text(encoding='utf-8'))",
                ],
            )
            assert "원격 자료" in result["stdout"]
            assert not (roots["default"] / "copy.txt").exists()
            await live["restart_agent"]()
            read = await call("fs_read", workspace_id="docs", path="moved.txt")
            assert read["text"] == "원격 자료\n"


async def test_workspace_binding_in_deduplication_and_approval(live: dict[str, Any]) -> None:
    http = live["client"]
    request = {
        "device_id": live["device_id"],
        "operation": "filesystem.copy",
        "payload": {"source": "source.txt", "destination": "copy.txt"},
        "execution_profile_id": "trusted_personal",
        "idempotency_key": "legacy-default-copy",
    }
    (live["workspace"] / "source.txt").write_text("one copy")
    original = await http.post("/api/v1/operations", json=request)
    assert original.json()["state"] == "SUCCEEDED", original.text
    # Pre-workspace canonical requests must retain their persisted default digest.
    normalized = {
        "principal_id": "owner_local",
        "device_id": live["device_id"],
        "operation": request["operation"],
        "payload": validate_payload(request["operation"], request["payload"]),
        "profile": "trusted_personal",
        "policy_revision": 1,
        "timeout_ms": 30000,
        "execution_mode": "sync",
    }
    row = live["app"].state.control.store.get(original.json()["operation_id"])
    assert row["payload_hash"] == canonical_digest(normalized)
    repeated = await http.post("/api/v1/operations", json={**request, "workspace_id": "default"})
    assert repeated.json() == original.json()
    for changed in [
        {"workspace_id": "docs"},
        {"payload": {**request["payload"], "destination_workspace_id": "docs"}},
    ]:
        conflict = await http.post("/api/v1/operations", json={**request, **changed})
        assert (
            conflict.status_code == 409
            and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        )
    assert not (live["workspaces"]["docs"] / "copy.txt").exists()
    approval_request = {
        "device_id": live["device_id"],
        "operation": "shell.exec",
        "workspace_id": "docs",
        "payload": {"argv": [sys.executable, "-c", "print('APPROVED-DOCS')"]},
        "execution_profile_id": "standard",
        "idempotency_key": "workspace-approval",
    }
    pending = await http.post("/api/v1/operations", json=approval_request)
    assert pending.json()["error"]["code"] == "APPROVAL_REQUIRED", pending.text
    ticket = pending.json()["error"]["details"]["approval_id"]
    listing = (await http.get("/api/v1/approvals")).json()["items"]
    assert next(a for a in listing if a["id"] == ticket)["target"]["workspace_id"] == "docs"
    assert (await http.post(f"/api/v1/approvals/{ticket}/approve")).status_code == 200
    wrong = await http.post(
        "/api/v1/operations",
        json={
            **approval_request,
            "workspace_id": "default",
            "approval_id": ticket,
        },
    )
    assert wrong.status_code == 409
    result = await http.post(f"/api/v1/approvals/{ticket}/execute")
    assert "APPROVED-DOCS" in result.json()["result"]["stdout"], result.text
    for selected, path in [("unknown", "."), ("default", str(live["workspaces"]["docs"]))]:
        denied = await http.post(
            "/api/v1/operations",
            json={
                "device_id": live["device_id"],
                "operation": "filesystem.list",
                "workspace_id": selected,
                "payload": {"path": path},
            },
        )
        assert denied.json()["error"]["code"] in {"PERMISSION_DENIED", "PATH_ACCESS_DENIED"}


async def test_artifact_cli_process_and_terminal_use_selected_folder(live: dict[str, Any]) -> None:
    docs = live["workspaces"]["docs"]
    raw = bytes(range(256)) * 1024
    source = live["workspace"].parent / "binary-input.bin"
    source.write_bytes(raw)
    artifact = await ArtifactClient(live["url"], live["owner"], live["device_id"]).upload(source)
    await remote(
        live,
        "filesystem.write",
        {"path": "binary.bin", "artifact_id": artifact["id"]},
        workspace_id="docs",
    )
    assert (docs / "binary.bin").read_bytes() == raw
    assert not (live["workspace"] / "binary.bin").exists()
    owner_store = live["workspace"].parent / "cli-owner.bin"
    SecretStore(owner_store).save({"token": live["owner"], "gateway": live["url"]})
    completed = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "racp_cli.main",
            "--gateway",
            live["url"],
            "--owner-store",
            str(owner_store),
            "fs",
            "stat",
            live["device_id"],
            "binary.bin",
            "--workspace-id",
            "docs",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert int(json.loads(completed.stdout)["result"]["size_bytes"]) == len(raw)
    assert live["owner"] not in completed.stdout + completed.stderr
    python = getattr(sys, "_base_executable", sys.executable)
    process = await remote(
        live,
        "process.spawn",
        {
            "argv": [
                python,
                "-c",
                "from pathlib import Path; import time; "
                "Path('process.txt').write_text('started'); time.sleep(90)",
            ]
        },
        workspace_id="docs",
    )
    try:
        for _ in range(100):
            if (docs / "process.txt").exists():
                break
            await asyncio.sleep(0.05)
        assert (docs / "process.txt").read_text() == "started"
        handle = (await live["client"].get("/api/v1/handles/" + process["handle"]["id"])).json()
        assert handle["workspace_id"] == "docs", handle
    finally:
        await remote(
            live,
            "process.terminate",
            {key: process[key] for key in ("pid", "create_time", "agent_boot_id")},
        )
    terminal = await remote(
        live, "terminal.open", {"argv": [python, "-q", "-i"]}, workspace_id="docs"
    )
    handle_id = terminal["handle_id"]
    try:
        handle = (await live["client"].get("/api/v1/handles/" + handle_id)).json()
        assert handle["workspace_id"] == "docs", handle
        # Later handle operations carry default; the original terminal cwd must persist.
        await remote(
            live,
            "terminal.write",
            {
                "handle_id": handle_id,
                "data": "from pathlib import Path; Path('terminal.txt').write_text('persistent'); "
                "print('TERM-'+'DOCS')\r",
            },
        )
        cursor, output = "0", ""
        for _ in range(100):
            read = await remote(
                live, "terminal.read", {"handle_id": handle_id, "cursor": cursor, "wait_ms": 100}
            )
            cursor = read["next_cursor"]
            output += read["data"]
            if "TERM-DOCS" in output:
                break
        assert "TERM-DOCS" in output and (docs / "terminal.txt").read_text() == "persistent"
        assert not (live["workspace"] / "terminal.txt").exists()
    finally:
        await remote(live, "terminal.close", {"handle_id": handle_id})
