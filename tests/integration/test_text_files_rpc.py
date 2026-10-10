import hashlib
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_policy.permissions import LocalPermissions, compile_permissions


async def test_text_search_and_patch_have_independent_mcp_grants(live: dict[str, Any]):
    agent, path = live["agent"], live["workspace"] / "own.txt"
    path.write_bytes(b"old\n")
    payload = {
        "files": [
            {
                "path": "own.txt",
                "expected_sha256": hashlib.sha256(b"old\n").hexdigest(),
                "edits": [{"old_text": "old", "new_text": "new"}],
            }
        ]
    }
    agent.permissions = compile_permissions(
        LocalPermissions(grants={"files.read.text": "allow", "files.edit": "allow"})
    )
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            patch = await client.call_tool(
                "fs_patch",
                {
                    "device_id": live["device_id"],
                    **payload,
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "owned-patch-denied",
                },
            )
            assert patch.is_error and path.read_bytes() == b"old\n"
            assert patch.structured_content["error"]["code"] == "PERMISSION_DENIED"
            agent.permissions = compile_permissions(
                LocalPermissions(
                    grants={
                        "files.patch": "allow",
                        "files.edit": "allow",
                        "files.read.text": "allow",
                        "files.search.content": "allow",
                    }
                )
            )
            patched = await client.call_tool(
                "fs_patch",
                {
                    "device_id": live["device_id"],
                    **payload,
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "owned-patch-allowed",
                },
            )
            assert not patched.is_error, patched
            assert path.read_bytes() == b"new\n"
            search = await client.call_tool(
                "fs_search_content", {"device_id": live["device_id"], "path": ".", "pattern": "new"}
            )
            assert not search.is_error, search
            assert len(search.structured_content["result"]["items"]) == 1
            assert search.structured_content["result"]["items"][0]["line"] == 1
