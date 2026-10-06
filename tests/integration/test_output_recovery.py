import asyncio
import hashlib
import sys
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import shell_request
from racp_domain.models import RACPError
from racp_sdk.artifacts import file_digest
from test_lifecycle import poll


async def attached(live: dict[str, Any], operation_id: str) -> dict[str, Any]:
    for _ in range(300):
        response = await live["client"].get(f"/api/v1/operations/{operation_id}/outputs")
        items = response.json()["items"]
        if items and items[0]["artifact_id"]:
            return items[0]
        await asyncio.sleep(0.05)
    raise AssertionError("pending output did not recover")


async def test_output_quota_recovery_survives_both_restarts_without_reexecution(
    live: dict[str, Any],
) -> None:
    manager = live["app"].state.artifacts
    manager.quota_bytes = 1
    code = (
        "from pathlib import Path; p=Path('output-counter'); "
        "p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('x'*200000)"
    )
    request = shell_request(live, [sys.executable, "-c", code], "recover-output")
    original = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert original["state"] == "SUCCEEDED"
    assert original["result"]["artifact_upload_status"] == "pending"
    operation_id = original["operation_id"]
    output = (await live["client"].get(f"/api/v1/operations/{operation_id}/outputs")).json()[
        "items"
    ][0]
    assert output["id"] == original["result"]["output_id"]
    forbidden = await live["client"].post(
        "/api/v1/artifact-transfers",
        headers={"Authorization": "Bearer " + live["credential"]},
        json={
            "operation_id": operation_id,
            "device_id": live["device_id"],
            "output_id": output["id"],
            "size_bytes": output["size_bytes"],
            "sha256": "0" * 64,
            "media_type": output["media_type"],
        },
    )
    assert forbidden.status_code == 403
    journal = live["agent"].journal
    journal.compact(
        now=datetime.now(UTC) + timedelta(days=2),
        pinned_operations=live["agent"].outputs.pinned_operations(),
    )
    assert journal.get(operation_id)["outcome_available"]
    await live["restart_agent"]()
    assert live["agent"].outputs.pending()[0]["id"] == output["id"]
    await live["restart_gateway"]()
    output = await attached(live, operation_id)
    content = await live["client"].get(f"/api/v1/artifacts/{output['artifact_id']}/content")
    assert hashlib.sha256(content.content).hexdigest() == output["sha256"]
    assert (live["workspace"] / "output-counter").read_text() == "1"
    assert (await live["client"].get(f"/api/v1/operations/{operation_id}")).json() == original
    assert (await live["client"].post("/api/v1/operations", json=request)).json() == original
    for _ in range(100):
        if not live["agent"].outputs.pending() and not list(live["agent"].spool.glob("*.output")):
            break
        await asyncio.sleep(0.05)
    assert not live["agent"].outputs.pending()
    assert not list(live["agent"].spool.glob("*.output"))


async def test_output_resume_uses_committed_transfer_after_agent_restart(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = live["app"].state.artifacts
    original_put = manager.put_bytes

    async def lost_ack(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await original_put(*args, **kwargs)
        raise RACPError("TRANSFER_INTERRUPTED", "injected chunk acknowledgement loss")

    monkeypatch.setattr(manager, "put_bytes", lost_ack)
    request = shell_request(
        live, [sys.executable, "-c", "import sys; sys.stdout.write('x'*9000000)"], "resume-output"
    )
    original = (await live["client"].post("/api/v1/operations", json=request)).json()
    operation_id = original["operation_id"]
    assert original["result"]["artifact_upload_status"] == "pending"
    row = live["agent"].outputs.pending()[0]
    transfer_id = row["transfer_id"]
    assert manager.transfer(transfer_id)["committed_bytes"] == 4 * 1024 * 1024
    await live["restart_agent"]()
    monkeypatch.setattr(manager, "put_bytes", original_put)
    output = await attached(live, operation_id)
    transfers = manager.store.db.execute(
        "SELECT * FROM artifact_transfers WHERE operation_id=?", (operation_id,)
    ).fetchall()
    assert len(transfers) == 1 and transfers[0]["id"] == transfer_id
    assert transfers[0]["state"] == "READY"
    assert transfers[0]["artifact_id"] == output["artifact_id"]
    assert (await live["client"].get(f"/api/v1/operations/{operation_id}")).json() == original


async def test_agent_spool_quota_rejects_before_mutation(live: dict[str, Any]) -> None:
    agent = live["agent"]
    agent.outputs.quota_bytes = 72 * 1024**2
    retained = agent.spool / "retained-output"
    retained.write_bytes(b"pending")
    request = shell_request(
        live, [sys.executable, "-c", "from pathlib import Path; Path('must-not-mutate').touch()"]
    )
    outcome = (await live["client"].post("/api/v1/operations", json=request)).json()
    assert outcome["state"] == "FAILED" and outcome["error"]["code"] == "RESOURCE_EXHAUSTED"
    assert outcome["error"]["execution_state"] == "not_started"
    assert not (live["workspace"] / "must-not-mutate").exists()
    assert retained.read_bytes() == b"pending" and not agent.outputs.reservations


async def test_deadline_during_output_hash_keeps_completed_execution(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()

    def delayed_hash(path: Any) -> tuple[int, str]:
        entered.set()
        assert release.wait(5)
        return file_digest(path)

    monkeypatch.setattr("racp_agent.runtime.file_digest", delayed_hash)
    request = shell_request(
        live,
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('completed-before-hash').touch(); print('x'*100000)",
        ],
        "hash-deadline",
        timeout_ms=1000,
        execution_mode="job",
    )
    accepted = (await live["client"].post("/api/v1/operations", json=request)).json()
    operation_id = accepted["operation_id"]
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(100):
            task = live["agent"].tasks.get(operation_id)
            if task and task.cancelling():
                break
            await asyncio.sleep(0.03)
        assert task and task.cancelling(), "deadline cancellation did not reach output preparation"
        release.set()
        outcome = await poll(live, operation_id)
        assert outcome["state"] == "SUCCEEDED", outcome
        assert (live["workspace"] / "completed-before-hash").exists()
        assert outcome["result"]["output_id"]
        await attached(live, operation_id)
        assert (await live["client"].get(f"/api/v1/operations/{operation_id}")).json() == outcome
    finally:
        release.set()
