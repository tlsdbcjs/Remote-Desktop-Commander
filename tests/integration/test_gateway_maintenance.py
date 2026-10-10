import json
from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.maintenance import MaintenanceCoordinator
from racp_gateway.service import ControlPlane
from racp_gateway.store import GatewayStore
from racp_protocol.management import ManagementPrincipal
from racp_protocol.models import OperationInput
from racp_sdk.security import digest


def owner() -> ManagementPrincipal:
    return ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)


@pytest.mark.asyncio
async def test_maintenance_blocks_new_admission_but_drain_ignores_closed_handles(
    tmp_path: Path,
) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    control = ControlPlane(store)
    coordinator = MaintenanceCoordinator(store, control)
    try:
        store.db.execute(
            "INSERT INTO handles(id,device_id,owner_id,boot_id,record,observed_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "hdl_closed",
                "dev_fixture",
                "owner_local",
                "boot_fixture",
                json.dumps({"state": "CLOSED"}),
                "2026-10-07T00:00:00Z",
            ),
        )
        report = await coordinator.drain(timeout_seconds=0)
        assert report.state == "READY"
        with pytest.raises(RACPError) as blocked:
            await control.execute(
                OperationInput(
                    device_id="dev_fixture",
                    operation="filesystem.stat",
                    payload={"path": "."},
                ),
                "owner_local",
            )
        assert blocked.value.error.code == "CAPABILITY_UNAVAILABLE"
        assert "maintenance" in blocked.value.error.message.lower()
    finally:
        coordinator.end()
        store.close()


@pytest.mark.asyncio
async def test_active_handle_defers_drain_and_job_key_is_idempotent(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    control = ControlPlane(store)
    coordinator = MaintenanceCoordinator(store, control)
    try:
        first = coordinator.begin(owner(), "backup", "nightly-1")
        second = coordinator.begin(owner(), "backup", "nightly-1")
        assert first.id == second.id
        store.db.execute(
            "INSERT INTO handles(id,device_id,owner_id,boot_id,record,observed_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "hdl_active",
                "dev_fixture",
                "owner_local",
                "boot_fixture",
                json.dumps({"state": "ACTIVE"}),
                "2026-10-07T00:00:00Z",
            ),
        )
        report = await coordinator.drain(timeout_seconds=0)
        assert report.state == "DEFERRED"
        assert report.active_handles == ["hdl_active"]
    finally:
        coordinator.end()
        store.close()
