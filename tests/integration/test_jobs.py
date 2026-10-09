import asyncio
import json
import subprocess
import sys
import time
import uuid
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from racp_domain.models import RACPError
from racp_sdk.artifacts import ArtifactClient
from racp_sdk.security import SecretStore


async def submit(
    live: dict[str, Any], operation: str, payload: dict[str, Any], **extra: Any
) -> Any:
    return await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": operation,
            "payload": payload,
            "execution_mode": "job",
            "execution_profile_id": "trusted_personal",
            "idempotency_key": uuid.uuid4().hex,
            **extra,
        },
    )


async def poll(live: dict[str, Any], job: str, state: str | None = None) -> dict[str, Any]:
    for _ in range(300):
        response = await live["client"].get("/api/v1/jobs/" + job)
        assert response.status_code == 200, response.text
        result = response.json()
        if (
            result["state"] == state
            if state is not None
            else result["state"] in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "UNKNOWN"}
        ):
            return result
        await asyncio.sleep(0.02)
    raise AssertionError("job state not reached: " + repr(result))


async def test_job_202_distinct_id_default_budget_list_and_replay(live: dict[str, Any]) -> None:
    response = await submit(
        live, "filesystem.write", {"path": "job.bin", "content": "once"}, idempotency_key="job-once"
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["state"] == "QUEUED" and accepted["job_id"] != accepted["operation_id"]
    assert accepted["poll_after_ms"] > 0 and "result" not in accepted
    done = await poll(live, accepted["job_id"])
    assert done["state"] == "COMPLETED" and done["timeout_ms"] == 3600000
    operation = (await live["client"].get("/api/v1/operations/" + accepted["operation_id"])).json()
    assert operation["state"] == "SUCCEEDED" and operation["job_id"] == accepted["job_id"]
    replay = await submit(
        live, "filesystem.write", {"path": "job.bin", "content": "once"}, idempotency_key="job-once"
    )
    assert replay.status_code == 202 and replay.json()["job_id"] == accepted["job_id"]
    assert replay.json()["state"] == "COMPLETED" and "result" not in replay.json()
    assert (live["workspace"] / "job.bin").read_text() == "once"
    late = (await live["client"].post("/api/v1/jobs/" + accepted["job_id"] + "/cancel")).json()
    assert late == done
    listed = (
        await live["client"].get("/api/v1/jobs", params={"device_id": live["device_id"]})
    ).json()
    assert listed["items"][0]["job_id"] == accepted["job_id"]
    assert (await live["client"].get("/api/v1/jobs/" + accepted["operation_id"])).status_code == 404


async def test_job_waiting_gateway_restart_retains_deadline_and_live_execution(
    live: dict[str, Any],
) -> None:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "process.spawn",
            "payload": {"argv": [sys.executable, "-c", "import time; time.sleep(30)"]},
            "idempotency_key": "wait-child",
            "execution_profile_id": "trusted_personal",
        },
    )
    target = response.json()["result"]
    response = await submit(
        live,
        "process.wait",
        {
            "pid": target["pid"],
            "create_time": target["create_time"],
            "agent_boot_id": target["agent_boot_id"],
        },
    )
    accepted = response.json()
    waiting = await poll(live, accepted["job_id"], "WAITING")
    assert waiting["waiting_reason"] == "process_exit"
    deadline = (
        live["app"]
        .state.control.store.db.execute(
            "SELECT deadline FROM operation_admission WHERE operation_id=?",
            (accepted["operation_id"],),
        )
        .fetchone()[0]
    )
    await live["restart_gateway"]()
    restored = await poll(live, accepted["job_id"], "WAITING")
    assert restored["waiting_reason"] == "process_exit" and restored["job_id"] == waiting["job_id"]
    assert (
        live["app"]
        .state.control.store.db.execute(
            "SELECT deadline FROM operation_admission WHERE operation_id=?",
            (accepted["operation_id"],),
        )
        .fetchone()[0]
        == deadline
    )
    stopped = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "process.terminate",
            "payload": {
                "pid": target["pid"],
                "create_time": target["create_time"],
                "agent_boot_id": target["agent_boot_id"],
                "force": True,
            },
            "idempotency_key": "finish-wait-child",
            "execution_profile_id": "trusted_personal",
        },
    )
    assert stopped.json()["state"] == "SUCCEEDED", stopped.text
    assert (await poll(live, accepted["job_id"]))["state"] == "COMPLETED"


async def test_bounded_queue_queued_cancel_timeout_and_fifo(
    live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, started = asyncio.Event(), asyncio.Event()
    count = 0
    calls: list[str] = []
    original = live["agent"].filesystem.execute

    async def blocked(
        operation: str, payload: dict[str, Any], context: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal count
        calls.append(payload["path"])
        if payload["path"].startswith("held-"):
            count += 1
            if count == 16:
                started.set()
            await gate.wait()
        return await original(operation, payload, context, **kwargs)

    monkeypatch.setattr(live["agent"].filesystem, "execute", blocked)
    held = []
    queued = []
    try:
        for index in range(16):
            response = await submit(
                live, "filesystem.write", {"path": f"held-{index}", "content": "held"}
            )
            assert response.status_code == 202
            held.append(response.json())
        await asyncio.wait_for(started.wait(), 5)
        canceled = (
            await submit(
                live, "filesystem.write", {"path": "canceled-before-start", "content": "no"}
            )
        ).json()
        result = (
            await live["client"].post("/api/v1/jobs/" + canceled["job_id"] + "/cancel")
        ).json()
        assert result["state"] == "CANCELLED"
        expired = (
            await submit(
                live,
                "filesystem.write",
                {"path": "expired-before-start", "content": "no"},
                timeout_ms=60,
            )
        ).json()
        assert (await poll(live, expired["job_id"]))["state"] == "TIMED_OUT"
        for index in range(64):
            response = await submit(
                live, "filesystem.write", {"path": f"queued-{index}", "content": "queued"}
            )
            assert response.status_code == 202, response.text
            queued.append(response.json())
        over = await submit(live, "filesystem.write", {"path": "over-cap", "content": "no"})
        assert over.status_code == 429 and over.json()["error"]["details"]["retry_after_ms"] > 0
        assert len(live["agent"].execution_inventory()) == 16
        gate.set()
        for item in held + queued:
            assert (await poll(live, item["job_id"]))["state"] == "COMPLETED"
        # Concurrent workers may enter providers in a different order after
        # their progress sends. The durable dispatch order must remain FIFO.
        dispatches = [
            row[0]
            for row in live["app"]
            .state.control.store.db.execute(
                "SELECT operation_id FROM audit WHERE event='dispatch' ORDER BY rowid"
            )
            .fetchall()
        ]
        assert dispatches[16:] == [item["operation_id"] for item in queued]
        assert len(calls[16:]) == 64 and set(calls[16:]) == {
            f"queued-{index}" for index in range(64)
        }
        assert (
            "canceled-before-start" not in calls
            and "expired-before-start" not in calls
            and "over-cap" not in calls
        )
        assert not (live["workspace"] / "canceled-before-start").exists()
        assert not (live["workspace"] / "expired-before-start").exists()
    finally:
        gate.set()


async def test_job_budget_limits_and_mcp_accepted_polling(live: dict[str, Any]) -> None:
    for mode, timeout in (("sync", 120001), ("job", 86400001)):
        invalid = await submit(
            live, "filesystem.stat", {"path": "."}, execution_mode=mode, timeout_ms=timeout
        )
        assert invalid.status_code == 400
    allowed = await submit(live, "filesystem.stat", {"path": "."}, timeout_ms=86400000)
    assert allowed.status_code == 202
    assert (await poll(live, allowed.json()["job_id"]))["timeout_ms"] == 86400000
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            rejected = await client.call_tool(
                "shell_exec",
                {
                    "device_id": live["device_id"],
                    "idempotency_key": "mcp-too-long",
                    "argv": [sys.executable, "--version"],
                    "execution_profile_id": "trusted_personal",
                    "timeout_ms": 20001,
                },
            )
            assert (
                rejected.is_error
                and rejected.structured_content["error"]["code"] == "INVALID_ARGUMENT"
            )
            accepted = await client.call_tool(
                "fs_stat", {"device_id": live["device_id"], "path": ".", "execution_mode": "job"}
            )
            assert not accepted.is_error and accepted.structured_content["job_id"].startswith(
                "job_"
            )
            job_id = accepted.structured_content["job_id"]
            for _ in range(100):
                done = await client.call_tool("job_get", {"job_id": job_id})
                if done.structured_content["state"] == "COMPLETED":
                    break
                await asyncio.sleep(0.02)
            assert not done.is_error and done.structured_content["state"] == "COMPLETED"


async def test_queued_jobs_survive_gateway_restart_without_redispatching_active_mutations(
    live: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, full = asyncio.Event(), asyncio.Event()
    count = 0
    original = live["agent"].filesystem.execute
    calls: list[str] = []

    async def held(
        operation: str, payload: dict[str, Any], context: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal count
        calls.append(payload["path"])
        if payload["path"].startswith("restart-held-"):
            count += 1
            if count == 16:
                full.set()
            await gate.wait()
        return await original(operation, payload, context, **kwargs)

    monkeypatch.setattr(live["agent"].filesystem, "execute", held)
    work = []
    try:
        for index in range(16):
            work.append(
                (
                    await submit(
                        live,
                        "filesystem.write",
                        {"path": f"restart-held-{index}", "content": "once"},
                    )
                ).json()
            )
        await asyncio.wait_for(full.wait(), 5)
        queued = (
            await submit(live, "filesystem.write", {"path": "restart-queued", "content": "once"})
        ).json()
        assert (await live["client"].get("/api/v1/jobs/" + queued["job_id"])).json()[
            "state"
        ] == "QUEUED"
        await live["restart_gateway"]()
        assert count == 16 and "restart-queued" not in calls
        gate.set()
        for item in work + [queued]:
            assert (await poll(live, item["job_id"]))["state"] == "COMPLETED"
        assert len(calls) == 17 and calls.count("restart-queued") == 1
    finally:
        gate.set()


async def test_cli_job_poll_list_and_late_cancel(live: dict[str, Any]) -> None:
    store = live["workspace"].parent / "job-cli-owner.bin"
    SecretStore(store).save({"token": live["owner"], "gateway": live["url"]})
    prefix = [
        sys.executable,
        "-m",
        "racp_cli.main",
        "--gateway",
        live["url"],
        "--owner-store",
        str(store),
    ]

    async def cli(*args: str) -> dict[str, Any]:
        result = await asyncio.to_thread(
            subprocess.run,
            [*prefix, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert live["owner"] not in result.stdout + result.stderr
        return json.loads(result.stdout)

    accepted = await cli("fs", "stat", live["device_id"], ".", "--job")
    assert accepted["job_id"].startswith("job_") and "result" not in accepted
    done = await poll(live, accepted["job_id"])
    assert done["state"] == "COMPLETED"
    queried = await cli("job", "get", accepted["job_id"])
    assert queried == done
    listed = await cli("job", "list", "--device-id", live["device_id"], "--limit", "1")
    assert listed["items"][0]["job_id"] == accepted["job_id"]
    assert await cli("job", "cancel", accepted["job_id"]) == done


async def test_cancel_intent_survives_gateway_restart_during_cleanup(
    live: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, cancelling, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def cleanup_barrier(
        operation: str, payload: dict[str, Any], context: Any
    ) -> dict[str, Any]:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await finish.wait()
            raise RACPError(
                "CANCELLED", "cleanup finished", layer="provider", cleanup_status="complete"
            ) from None
        raise AssertionError("unreachable")

    monkeypatch.setattr(live["agent"].filesystem, "execute", cleanup_barrier)
    accepted = (
        await submit(live, "filesystem.write", {"path": "cancel-restart", "content": "never"})
    ).json()
    try:
        await asyncio.wait_for(entered.wait(), 3)
        response = await live["client"].post("/api/v1/jobs/" + accepted["job_id"] + "/cancel")
        assert response.json()["state"] == "CANCEL_REQUESTED"
        await asyncio.wait_for(cancelling.wait(), 3)
        first_sent = (
            live["app"]
            .state.control.store.db.execute(
                "SELECT cancel_sent FROM operation_admission WHERE operation_id=?",
                (accepted["operation_id"],),
            )
            .fetchone()[0]
        )
        await live["restart_gateway"]()
        assert (
            live["app"]
            .state.control.store.db.execute(
                "SELECT cancel_sent FROM operation_admission WHERE operation_id=?",
                (accepted["operation_id"],),
            )
            .fetchone()[0]
            == first_sent
        )
        recovered = (await live["client"].get("/api/v1/jobs/" + accepted["job_id"])).json()
        assert recovered["state"] == "CANCEL_REQUESTED", recovered
        finish.set()
        assert (await poll(live, accepted["job_id"]))["state"] == "CANCELLED"
        assert not (live["workspace"] / "cancel-restart").exists()
    finally:
        finish.set()


async def test_queued_job_pins_input_artifact_until_terminal_state(live: dict[str, Any]) -> None:
    source = live["workspace"].parent / "queued-input.bin"
    source.write_bytes(b"queued binary input")
    artifact = await ArtifactClient(live["url"], live["owner"], live["device_id"]).upload(source)
    control = live["app"].state.control
    await control.scheduler.close()  # Deterministic dispatch barrier under the real API.
    response = await submit(
        live, "filesystem.write", {"path": "queued-target", "artifact_id": artifact["id"]}
    )
    assert response.status_code == 202
    job = response.json()
    manager = live["app"].state.artifacts
    manager.store.db.execute(
        "UPDATE artifacts SET expires=? WHERE id=?", (time.time() - 1, artifact["id"])
    )
    await manager.collect()
    assert manager.get(artifact["id"], "owner_local")["state"] == "READY"
    assert (manager.root / artifact["sha256"]).read_bytes() == source.read_bytes()
    canceled = await live["client"].post("/api/v1/jobs/" + job["job_id"] + "/cancel")
    assert canceled.json()["state"] == "CANCELLED"
    await manager.collect()
    assert manager.get(artifact["id"], "owner_local")["state"] == "DELETED"
    assert not (manager.root / artifact["sha256"]).exists()
    assert not (live["workspace"] / "queued-target").exists()
