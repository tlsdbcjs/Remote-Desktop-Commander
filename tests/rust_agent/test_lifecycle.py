import asyncio
import json

import pytest
from conftest import bridge
from racp_sdk.security import SecretStore

pytestmark = pytest.mark.asyncio


async def test_existing_python_registration_start_status_stop(rust_live):
    live = rust_live
    info = await bridge(live["executable"], live["state"], "info")
    assert info["device_id"] == live["device_id"]
    assert "credential" not in info
    started = await bridge(live["executable"], live["state"], "start")
    assert started["state"] == "RUNNING"
    for _ in range(200):
        device = (await live["http"].get("/api/v1/devices/" + live["device_id"])).json()
        if device["info"].get("status") == "ONLINE":
            break
        await asyncio.sleep(0.05)
    assert device["info"]["status"] == "ONLINE"
    status = await bridge(live["executable"], live["state"], "status")
    assert status["connected"]
    again = await bridge(live["executable"], live["state"], "start")
    assert again["pid"] == started["pid"]
    result = await bridge(live["executable"], live["state"], "stop")
    assert result["state"] == "STOPPED" and result["cleanup_status"] == "complete"
    assert (await bridge(live["executable"], live["state"], "status"))["state"] == "STOPPED"
    assert SecretStore(live["state"] / "credential.bin").load()["device_id"] == live["device_id"]


async def test_stale_control_instance_rejected(rust_live):
    live = rust_live
    await bridge(live["executable"], live["state"], "start")
    control = live["state"] / "background" / "control.bin"
    saved = SecretStore(control).load()
    record = json.loads(saved["record"])
    record["instance_id"] = "wrong_instance_id_123456"
    SecretStore(control).save({"record": json.dumps(record)})
    try:
        with pytest.raises(RuntimeError):
            await bridge(live["executable"], live["state"], "status")
    finally:
        SecretStore(control).save(saved)
        assert (await bridge(live["executable"], live["state"], "stop"))[
            "cleanup_status"
        ] == "complete"


async def test_existing_python_journal_result_reconciles_without_execution(rust_live):
    from racp_sdk.journal import Journal
    from racp_sdk.security import canonical_digest, digest
    from test_artifacts import operation

    live = rust_live
    op = operation(live, "shell.exec", {"argv": ["must-not-run"]}, "golden")
    store = live["app"].state.control.store
    request = store.get(op)["request"]
    normalized = {
        "operation": request["operation"],
        "payload": request["payload"],
        "context": {
            key: value for key, value in request["context"].items() if key != "workspace_id"
        },
        "timeout_ms": request["timeout_ms"],
        "execution_mode": request["execution_mode"],
    }
    journal = Journal(live["state"] / "data" / "execution.db")
    journal.accept(
        digest("owner_local:" + live["device_id"]),
        digest(request["idempotency_key"]),
        canonical_digest(normalized),
        request,
    )
    journal.transition(op, "SUCCEEDED", result={"golden": "한글 preserved"})
    journal.close()
    await bridge(live["executable"], live["state"], "start")
    for _ in range(200):
        record = store.get(op)
        if record["state"] == "SUCCEEDED":
            break
        await asyncio.sleep(0.05)
    assert record["state"] == "SUCCEEDED" and record["result"] == {"golden": "한글 preserved"}
    # The executable is intentionally invalid, so success can only come from replay.
    assert (await bridge(live["executable"], live["state"], "stop"))["cleanup_status"] == "complete"
