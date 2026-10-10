from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.management.devices import DeviceManagement
from racp_gateway.store import GatewayStore
from racp_protocol.management import DeviceQuery, ManagementPrincipal
from racp_sdk.security import digest


def test_twenty_agents_have_independent_ids_epochs_and_groups(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    principal = ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)
    management = DeviceManagement(store)
    try:
        ids: list[str] = []
        for index in range(20):
            enrolled = store.enroll(store.enrollment(f"agent-{index}"))
            ids.append(enrolled["device_id"])
            assert store.connected(enrolled["device_id"], {"status": "CONNECTING"}) == 1
        group = management.create_group(principal, "lab")
        changed = management.set_groups(principal, ids[0], [group], 1)
        assert changed.group_ids == [group] and changed.revision == 2
        page = management.list(principal, DeviceQuery(limit=200))
        assert len(page.items) == 20
        assert len({item.id for item in page.items}) == 20
        assert {item.epoch for item in page.items} == {1}
        assert next(item for item in page.items if item.id == ids[0]).group_ids == [group]
    finally:
        store.close()


def test_offline_agent_does_not_block_other_agents_and_cursor_is_scoped(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    principal = ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)
    management = DeviceManagement(store)
    try:
        ids = [store.enroll(store.enrollment(f"agent-{index}"))["device_id"] for index in range(3)]
        store.device_status(ids[0], "OFFLINE")
        store.device_status(ids[1], "ONLINE")
        online = management.list(principal, DeviceQuery(status="ONLINE", limit=1))
        assert [item.id for item in online.items] == [ids[1]]
        first = management.list(principal, DeviceQuery(limit=1))
        assert first.next_cursor
        with pytest.raises(RACPError) as cursor_error:
            management.list(principal, DeviceQuery(limit=2, cursor=first.next_cursor))
        assert cursor_error.value.error.code == "CURSOR_EXPIRED"
    finally:
        store.close()


def test_revoked_agent_cannot_reconnect(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    try:
        enrollment = store.enroll(store.enrollment("revoked"))
        store.revoke(enrollment["device_id"])
        with pytest.raises(RACPError) as denied:
            store.connected(enrollment["device_id"], {"status": "CONNECTING"})
        assert denied.value.error.code == "DEVICE_REVOKED"
    finally:
        store.close()
