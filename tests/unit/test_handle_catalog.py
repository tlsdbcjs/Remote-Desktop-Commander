from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.handles import HandleCatalog
from racp_gateway.store import GatewayStore
from racp_protocol.models import ResourceHandle, timestamp
from racp_sdk.security import digest, token


def test_handle_scope_old_revision_and_new_boot_expiry(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest(token()))
    device = store.enroll(store.enrollment("fixture"))["device_id"]
    catalog = HandleCatalog(store)
    now = timestamp()
    handle = ResourceHandle(
        id="term_fixture",
        type="terminal",
        device_id=device,
        owner="owner_local",
        agent_boot_id="boot_old",
        provider_instance_id="provider_fixture",
        resource_revision="1",
        created_at=now,
        last_access_at=now,
        expires_at=now,
        state="ACTIVE",
        availability="available",
    )
    try:
        catalog.observe([handle], device, "boot_old", complete=True)
        with pytest.raises(RACPError) as denied:
            catalog.observe(
                [handle.model_copy(update={"owner": "other"})], device, "boot_old", complete=False
            )
        assert denied.value.error.code == "PERMISSION_DENIED"
        closed = handle.model_copy(update={"state": "CLOSED", "resource_revision": "2"})
        catalog.observe([closed], device, "boot_old", complete=False)
        catalog.observe([handle], device, "boot_old", complete=False)
        assert catalog.get(handle.id, "owner_local")["state"] == "CLOSED"
        second = handle.model_copy(update={"id": "term_second"})
        catalog.observe([second], device, "boot_old", complete=False)
        catalog.offline(device)
        assert catalog.get(second.id, "owner_local")["availability"] == "offline"
        catalog.observe([], device, "boot_new", complete=True)
        assert catalog.get(second.id, "owner_local")["state"] == "EXPIRED"
        catalog.result({"handle": second.model_dump()}, device, "boot_new")
        assert catalog.get(second.id, "owner_local")["state"] == "EXPIRED"
    finally:
        store.close()
