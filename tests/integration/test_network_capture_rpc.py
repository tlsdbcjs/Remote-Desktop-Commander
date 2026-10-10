"""Gateway/MCP to a real contained fixture worker; native NIC acceptance is separate."""

import hashlib
import os
import struct
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_agent.plugins.process import ContainedCommand
from racp_policy.permissions import LocalPermissions, compile_permissions

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows capture containment")


async def test_capture_rpc_and_mcp_export_scoped_pcap_without_execution_grant(
    live: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = bytes.fromhex(
        "450000280001000040060000c0a81d79c0a81d8dc3cb223d00000001000000005010040000000000"
    )
    pcap = (
        struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101)
        + struct.pack("<IIII", 1, 0, 40, 40)
        + packet
    )
    worker = tmp_path / "owned_packet_worker.py"
    worker.write_text(
        "import hashlib,json,sys;from pathlib import Path\n"
        f"data={pcap!r};Path(sys.argv[1]).write_bytes(data)\n"
        "print(json.dumps({'status':'SUCCEEDED','local_ip':'192.168.29.121',"
        "'peer_ip':'192.168.29.141','local_port':50123,'packets':1,'bytes':len(data),"
        "'sha256':hashlib.sha256(data).hexdigest(),'raw_socket_closed':True,"
        "'link_type':'raw_ipv4','tls_decryption':False,'termination_reason':'duration'}))\n",
        encoding="utf-8",
    )
    assert hasattr(live["agent"], "network_capture"), "Capture is not wired into the Agent"
    provider = live["agent"].network_capture
    monkeypatch.setattr(
        provider,
        "command",
        lambda data, path: ContainedCommand(
            command=[sys.executable, "-I", str(worker), str(path)], working_directory=str(tmp_path)
        ),
    )
    body = {
        "device_id": live["device_id"],
        "operation": "network.capture",
        "payload": {
            "local_ip": "192.168.29.121",
            "peer_ip": "192.168.29.141",
            "local_port": 50123,
            "duration_ms": 100,
            "max_bytes": 4096,
        },
        "execution_profile_id": "trusted_personal",
        "idempotency_key": "capture-denied",
    }
    for grants in ({}, {"network.capture.ipv4": "allow"}, {"artifacts.export": "allow"}):
        live["agent"].permissions = compile_permissions(LocalPermissions(grants=grants))
        body["idempotency_key"] += "x"
        denied = await live["client"].post("/api/v1/operations", json=body)
        assert denied.status_code == 200, denied.text
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    live["agent"].permissions = compile_permissions(
        LocalPermissions(grants={"network.capture.ipv4": "allow", "artifacts.export": "allow"})
    )
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            result = await client.call_tool(
                "network_capture",
                {
                    **body["payload"],
                    "device_id": live["device_id"],
                    "execution_profile_id": "trusted_personal",
                    "idempotency_key": "capture-mcp-allowed",
                },
            )
            assert not result.is_error, result.structured_content
            value = result.structured_content["result"]
    assert value["sha256"] == hashlib.sha256(pcap).hexdigest()
    assert value["device_id"] == live["device_id"] and value["cleanup_status"] == "complete"
    artifact = await live["client"].get("/api/v1/artifacts/" + value["artifact_id"] + "/content")
    assert artifact.status_code == 200 and artifact.content == pcap
    assert not provider.pending and not provider.protected_pids()
