import hashlib
import time
from typing import Any


async def test_device_keyset_cursor_binds_filters_limit_and_restart(live: dict[str, Any]) -> None:
    client = live["client"]
    for index in range(5):
        setup = (
            await client.post("/api/v1/enrollment-tokens", json={"name": f"page-device-{index}"})
        ).json()
        assert (
            await client.post("/agent/v1/enroll", json={"token": setup["token"]})
        ).status_code == 200
    first = (await client.get("/api/v1/devices?limit=2&state=OFFLINE")).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    cursor = first["next_cursor"]
    # A new row inserted above the first page does not duplicate/shift the next page.
    setup = (
        await client.post("/api/v1/enrollment-tokens", json={"name": "inserted-between-pages"})
    ).json()
    await client.post("/agent/v1/enroll", json={"token": setup["token"]})
    second = (
        await client.get(
            "/api/v1/devices", params={"limit": 2, "state": "OFFLINE", "cursor": cursor}
        )
    ).json()
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    assert all(row["name"] != "inserted-between-pages" for row in second["items"])
    assert first["consistency"] == "best_effort" and first["sort"] == "rowid_desc"
    # Random signatures can already begin with 'x'; always change an encoded bit.
    tampered = ("y" if cursor.startswith("x") else "x") + cursor[1:]
    assert tampered != cursor
    for params in (
        {"limit": 3, "state": "OFFLINE", "cursor": cursor},
        {"limit": 2, "state": "ONLINE", "cursor": cursor},
        {"limit": 2, "state": "OFFLINE", "cursor": tampered},
    ):
        assert (await client.get("/api/v1/devices", params=params)).status_code == 410
    assert (await client.get("/api/v1/devices?limit=501")).status_code == 400
    await live["restart_gateway"]()
    assert (
        await client.get(
            "/api/v1/devices", params={"limit": 2, "state": "OFFLINE", "cursor": cursor}
        )
    ).status_code == 410


async def test_job_approval_artifact_and_audit_pages_are_scoped(live: dict[str, Any]) -> None:
    client = live["client"]
    requests = []
    for index in range(4):
        body = {
            "device_id": live["device_id"],
            "operation": "filesystem.write",
            "payload": {"path": f"list-{index}", "content": "once"},
            "idempotency_key": f"page-{index}",
            "execution_mode": "job",
            "execution_profile_id": "standard",
        }
        denied = await client.post("/api/v1/operations", json=body)
        assert denied.status_code == 409
        requests.append(denied.json()["error"]["details"]["approval_id"])
    approvals = (await client.get("/api/v1/approvals?state=PENDING&limit=2")).json()
    assert len(approvals["items"]) == 2 and approvals["next_cursor"]
    await client.post(f"/api/v1/approvals/{requests[0]}/approve")
    await client.post(f"/api/v1/approvals/{requests[0]}/execute")
    store = live["app"].state.control.store
    store.db.execute("UPDATE approvals SET expires=? WHERE id=?", (time.time() - 1, requests[1]))
    expired = (await client.get("/api/v1/approvals?state=EXPIRED")).json()
    assert any(item["id"] == requests[1] for item in expired["items"])
    for index in range(3):
        body = {
            "device_id": live["device_id"],
            "operation": "filesystem.stat",
            "payload": {"path": "."},
            "execution_mode": "job",
            "idempotency_key": f"read-page-{index}",
        }
        assert (await client.post("/api/v1/operations", json=body)).status_code == 202
    jobs = (await client.get("/api/v1/jobs?limit=2")).json()
    assert jobs["next_cursor"] and len(jobs["items"]) == 2
    second = (
        await client.get("/api/v1/jobs", params={"limit": 2, "cursor": jobs["next_cursor"]})
    ).json()
    assert not {row["id"] for row in jobs["items"]} & {row["id"] for row in second["items"]}
    for _ in range(100):
        completed = (await client.get("/api/v1/jobs?state=COMPLETED")).json()
        if len(completed["items"]) >= 3:
            break
        import asyncio

        await asyncio.sleep(0.02)
    assert all(row["state"] == "COMPLETED" for row in completed["items"])
    manager = live["app"].state.artifacts
    from racp_protocol.artifacts import TransferComplete, TransferCreate

    for _ in range(3):
        transfer = manager.create(
            TransferCreate(
                device_id=live["device_id"], size_bytes=0, sha256=hashlib.sha256(b"").hexdigest()
            ),
            owner="owner_local",
        )
        await manager.complete(
            transfer["id"],
            transfer["credential"],
            TransferComplete(size_bytes=0, sha256=transfer["sha256"]),
        )
    files = (await client.get("/api/v1/artifacts?state=READY&limit=2")).json()
    assert len(files["items"]) == 2 and files["next_cursor"]
    assert (
        await client.get("/api/v1/artifacts", params={"limit": 2, "cursor": jobs["next_cursor"]})
    ).status_code == 410
    store.audit("must_not_show", {"context": {"principal_id": "other_owner"}})
    audited = (await client.get("/api/v1/audit?limit=2")).json()
    assert audited["next_cursor"] and all(
        item["owner_id"] == "owner_local" for item in audited["items"]
    )
    assert (await client.get("/api/v1/audit?event=must_not_show")).json()["items"] == []
    assert (await client.get("/api/v1/artifacts?device_id=dev_other")).status_code == 403
