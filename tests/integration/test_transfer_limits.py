import asyncio
import hashlib
import time
from typing import Any

from racp_sdk.artifacts import ArtifactClient
from test_artifact_transfers import chunk, complete, create


async def test_device_transfer_limit_survives_restart_and_renew_cannot_bypass(
    live: dict[str, Any],
) -> None:
    client = live["client"]
    data = b"bounded-transfer"
    payload = {
        "device_id": live["device_id"],
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    responses = await asyncio.gather(
        *[client.post("/api/v1/artifact-transfers", json=payload) for _ in range(5)]
    )
    transfers = [item.json() for item in responses if item.status_code == 200]
    assert len(transfers) == 2
    assert [item.status_code for item in responses].count(429) == 3
    assert all(
        item.json()["error"]["details"]["device_limit"] == 2
        for item in responses
        if item.status_code == 429
    )
    first, second = transfers
    assert (
        await client.post(f"/api/v1/artifact-transfers/{first['id']}/authorize")
    ).status_code == 200
    await live["restart_gateway"]()
    assert (await client.post("/api/v1/artifact-transfers", json=payload)).status_code == 429
    manager = live["app"].state.artifacts
    manager.store.db.execute(
        "UPDATE artifact_transfers SET credential_expires=? WHERE id=?",
        (time.time() - 1, first["id"]),
    )
    third = await create(live, data)
    assert (
        await client.post(f"/api/v1/artifact-transfers/{first['id']}/authorize")
    ).status_code == 429
    await chunk(live, second, data)
    assert (await complete(live, second)).status_code == 200
    first = (await client.post(f"/api/v1/artifact-transfers/{first['id']}/authorize")).json()
    await chunk(live, first, data)
    assert (await complete(live, first)).status_code == 200
    await chunk(live, third, data)
    assert (await complete(live, third)).status_code == 200


async def test_download_verified_ack_releases_capacity_and_resume_reclaims_it(
    live: dict[str, Any],
) -> None:
    client = live["client"]
    data = b"download-capacity"
    transfer = await create(live, data)
    await chunk(live, transfer, data)
    artifact = (await complete(live, transfer)).json()
    payload = {
        "device_id": live["device_id"],
        "direction": "download",
        "artifact_id": artifact["id"],
    }
    first = (await client.post("/api/v1/artifact-transfers", json=payload)).json()
    second = (await client.post("/api/v1/artifact-transfers", json=payload)).json()
    assert (await client.post("/api/v1/artifact-transfers", json=payload)).status_code == 429
    wrong = await client.post(
        f"/api/v1/artifact-transfers/{first['id']}/complete",
        headers={"Authorization": "Bearer " + first["credential"]},
        json={"size_bytes": len(data), "sha256": "0" * 64},
    )
    assert wrong.status_code == 412
    assert (await client.post("/api/v1/artifact-transfers", json=payload)).status_code == 429
    acknowledged = await complete(live, first)
    assert acknowledged.json()["state"] == "DOWNLOAD_COMPLETE"
    assert (await complete(live, first)).json() == acknowledged.json()
    assert (
        await client.get(
            f"/api/v1/artifact-transfers/{first['id']}/content",
            headers={"Authorization": "Bearer " + first["credential"]},
        )
    ).status_code == 409
    third = (await client.post("/api/v1/artifact-transfers", json=payload)).json()
    assert (
        await client.post(f"/api/v1/artifact-transfers/{first['id']}/authorize")
    ).status_code == 429
    await complete(live, second)
    renewed = (await client.post(f"/api/v1/artifact-transfers/{first['id']}/authorize")).json()
    assert renewed["state"] == "DOWNLOAD"
    await complete(live, renewed)
    await complete(live, third)
    sdk = ArtifactClient(live["url"], live["owner"], live["device_id"])
    for index in range(3):
        path = live["workspace"] / f"download-{index}"
        result = await sdk.download(artifact["id"], path)
        assert path.read_bytes() == data
        state = (await client.get(f"/api/v1/artifact-transfers/{result['transfer_id']}")).json()
        assert state["state"] == "DOWNLOAD_COMPLETE"


async def test_device_cannot_renew_unassigned_owner_transfer(live: dict[str, Any]) -> None:
    transfer = await create(live, b"owner upload")
    headers = {"Authorization": "Bearer " + live["credential"]}
    client = live["client"]
    assert (
        await client.get(f"/api/v1/artifact-transfers/{transfer['id']}", headers=headers)
    ).status_code == 403
    assert (
        await client.post(f"/api/v1/artifact-transfers/{transfer['id']}/authorize", headers=headers)
    ).status_code == 403
    assert (
        await client.post(f"/api/v1/artifact-transfers/{transfer['id']}/authorize")
    ).status_code == 200
