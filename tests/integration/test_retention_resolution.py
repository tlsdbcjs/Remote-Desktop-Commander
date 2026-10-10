import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from racp_domain.models import RACPError
from racp_protocol.models import Request, Result


async def test_retired_transient_read_reconciles_without_reviving_or_accepting_forgery(
    live: dict[str, Any],
) -> None:
    client = live["client"]
    body = {
        "device_id": live["device_id"],
        "operation": "filesystem.stat",
        "payload": {"path": "."},
    }
    first = (await client.post("/api/v1/operations", json=body)).json()
    assert first["state"] == "SUCCEEDED"
    operation_id = first["operation_id"]
    control = live["app"].state.control
    record = live["agent"].journal.get(operation_id)
    assert not record["retain_key"] and not record["mutation"]
    control.store.compact(now=datetime.now(UTC) + timedelta(days=2))
    assert (await client.get("/api/v1/operations/" + operation_id)).status_code == 404
    await live["restart_agent"]()
    replay = live["agent"].result_message(live["agent"].journal.get(operation_id))
    assert isinstance(replay, Result)
    connection = control.connections[live["device_id"]]
    control.accept_result(replay, connection, reconcile=True)
    for change in [
        {"request_id": "req_forged"},
        {"trace_id": "0" * 32},
        {"device_id": "dev_other"},
        {"connection_epoch": replay.connection_epoch + 1},
    ]:
        with pytest.raises(RACPError):
            control.accept_result(replay.model_copy(update=change), connection, reconcile=True)
    with pytest.raises(RACPError):
        control.accept_result(replay, connection)
    assert (await client.get("/api/v1/operations/" + operation_id)).status_code == 404
    assert (await client.post("/api/v1/operations", json=body)).json()["state"] == "SUCCEEDED"


async def test_expired_mutation_returns_410_and_never_reexecutes_after_restart(
    live: dict[str, Any],
) -> None:
    body = {
        "device_id": live["device_id"],
        "operation": "filesystem.write",
        "payload": {"path": "retained.txt", "content": "once"},
        "idempotency_key": "retain-once",
        "execution_profile_id": "trusted_personal",
    }
    first = (await live["client"].post("/api/v1/operations", json=body)).json()
    assert first["state"] == "SUCCEEDED"
    operation_id = first["operation_id"]
    live["app"].state.control.store.compact(now=datetime.now(UTC) + timedelta(days=2))
    live["agent"].journal.compact(now=datetime.now(UTC) + timedelta(days=2))
    expired = await live["client"].get("/api/v1/operations/" + operation_id)
    assert (
        expired.status_code == 410
        and expired.json()["error"]["details"]["operation_id"] == operation_id
    )
    replay = await live["client"].post("/api/v1/operations", json=body)
    assert replay.status_code == 410 and replay.json()["error"]["code"] == "OPERATION_EXPIRED"
    await live["restart_gateway"]()
    assert (await live["client"].post("/api/v1/operations", json=body)).status_code == 410
    assert (live["workspace"] / "retained.txt").read_text() == "once"
    tomb = (
        live["app"]
        .state.control.store.db.execute("SELECT operation_id FROM idempotency_tombstones")
        .fetchone()
    )
    assert tomb[0] == operation_id


async def test_agent_expired_outcome_message_keeps_connection_and_no_new_execution(
    live: dict[str, Any],
) -> None:
    body = {
        "device_id": live["device_id"],
        "operation": "filesystem.write",
        "payload": {"path": "agent-retained", "content": "once"},
        "idempotency_key": "agent-retain-once",
        "execution_profile_id": "trusted_personal",
    }
    first = (await live["client"].post("/api/v1/operations", json=body)).json()
    operation_id = first["operation_id"]
    record = live["agent"].journal.get(operation_id)
    request = Request.model_validate(record["request"]).model_copy(
        update={"connection_epoch": live["agent"].epoch}
    )
    live["agent"].journal.compact(now=datetime.now(UTC) + timedelta(days=2))
    await live["agent"].dispatch(request)
    await asyncio.sleep(0.05)
    assert live["agent"].socket is not None
    assert (await live["client"].get("/api/v1/operations/" + operation_id)).json() == first
    assert (live["workspace"] / "agent-retained").read_text() == "once"


async def test_late_result_is_resolution_without_overwriting_unknown(
    live: dict[str, Any], monkeypatch: Any
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = live["agent"].filesystem.execute

    async def delayed(
        operation: str, payload: dict[str, Any], context: Any, **kwargs: Any
    ) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return await original(operation, payload, context, **kwargs)

    monkeypatch.setattr(live["agent"].filesystem, "execute", delayed)
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "filesystem.write",
            "payload": {"path": "late-result", "content": "once"},
            "idempotency_key": "late-result-once",
            "execution_profile_id": "trusted_personal",
            "execution_mode": "job",
        },
    )
    accepted = response.json()
    try:
        await asyncio.wait_for(entered.wait(), 3)
        store = live["app"].state.control.store
        store.transition(
            accepted["operation_id"],
            "UNKNOWN",
            error={"code": "EXECUTION_UNKNOWN", "message": "injected confirmation loss"},
        )
        release.set()
        for _ in range(100):
            resolutions = (
                await live["client"].get(
                    "/api/v1/operations/" + accepted["operation_id"] + "/resolutions"
                )
            ).json()
            if resolutions["items"]:
                break
            await asyncio.sleep(0.02)
        assert resolutions["state"] == "UNKNOWN" and len(resolutions["items"]) == 1
        assert resolutions["items"][0]["state"] == "SUCCEEDED"
        job = (await live["client"].get("/api/v1/jobs/" + accepted["job_id"])).json()
        assert job["state"] == "UNKNOWN" and job["result"] is None
        assert (live["workspace"] / "late-result").read_text() == "once"
        record = live["agent"].journal.get(accepted["operation_id"])
        result = live["agent"].result_message(record)
        assert isinstance(result, Result)
        await live["agent"].send(result)
        await asyncio.sleep(0.05)
        assert len(store.resolutions(accepted["operation_id"])) == 1
    finally:
        release.set()
