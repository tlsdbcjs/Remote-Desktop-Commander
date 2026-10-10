import io
import zipfile
from pathlib import Path

import httpx
import pytest
from racp_domain.version import VERSION
from racp_gateway.app import create_app
from racp_gateway.config import GatewayConfig, write_gateway_config
from racp_gateway.console_auth import COOKIE, ConsoleAuth
from racp_gateway.events import EventFeed
from racp_gateway.management.devices import DeviceManagement
from racp_gateway.management.status import GatewayStatusService
from racp_gateway.management.store import ManagementStore
from racp_gateway.store import GatewayStore
from racp_protocol.management import DeviceQuery, ManagementPrincipal
from racp_sdk.security import digest

from scripts.gateway_management_acceptance import collect_snapshot, validate_snapshot
from scripts.gateway_management_fixture import prepare_fixture


def test_acceptance_scenarios_use_management_api_without_secret_echo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status") and "updates" not in request.url.path:
            return httpx.Response(
                200,
                json={
                    "ready": True,
                    "database": "ready",
                    "connected_devices": 20,
                    "version": VERSION,
                },
            )
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.url.path.endswith("/updates/status"):
            return httpx.Response(200, json={"apply_enabled": False})
        if request.url.path.endswith("/backups"):
            return httpx.Response(
                200,
                json=[{"id": "bak_fixture"}],
            )
        if request.url.path.endswith("/restore-preview"):
            return httpx.Response(
                200,
                json={"backup_id": "bak_fixture", "valid": True},
            )
        return httpx.Response(404)

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://gateway.example",
    ) as client:
        multi = collect_snapshot(client, "multi-agent")
        upgrade = collect_snapshot(client, "upgrade")
        restore = collect_snapshot(client, "restore")
        soak = collect_snapshot(client, "soak", duration_seconds=0)
    assert multi["status"]["connected_devices"] == 20
    assert upgrade["update"]["apply_enabled"] is False
    assert restore["restore_previews"][0]["valid"] is True
    assert len(soak["samples"]) == 1
    validate_snapshot(multi, expected_version=VERSION)


@pytest.mark.asyncio
async def test_fifty_synthetic_agents_ten_browser_sessions_and_sse_slots_are_isolated(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    config_path = state / "config" / "gateway.json"
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_acceptance_fixture",
            state_root=str(state),
            public_origin="http://127.0.0.1:8765",
            port=8765,
        ),
    )
    store = GatewayStore(state / "gateway.db")
    store.initialize(digest("owner"))
    auth = ConsoleAuth(store)
    events = EventFeed(store)
    principal = ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)
    streams = []
    releases = []
    try:
        device_ids = []
        for index in range(50):
            enrolled = store.enroll(store.enrollment(f"synthetic-{index}"))
            device_ids.append(enrolled["device_id"])
            store.connected(enrolled["device_id"], {"status": "ONLINE"})
            store.device_status(enrolled["device_id"], "ONLINE")
        store.device_status(device_ids[-1], "OFFLINE")

        sessions = [auth.create_session("owner_local") for _ in range(10)]
        assert len({credential for credential, _ in sessions}) == 10
        for credential, _ in sessions:
            stream, release = events.stream(
                "owner_local",
                None,
                lambda value=credential: auth.authenticate(value),
            )
            streams.append(stream)
            releases.append(release)

        page = DeviceManagement(store).list(principal, DeviceQuery(limit=200))
        assert len(page.items) == 50
        assert len({item.id for item in page.items}) == 50
        status = GatewayStatusService(
            store,
            ManagementStore(store),
            config_path=config_path,
            oauth_configured=False,
            event_stream_count=lambda: sum(events.active.values()),
        ).snapshot(principal)
        assert status.total_devices == 50
        assert status.connected_devices == 49
        assert status.event_streams == 10

        store.revoke(device_ids[0])
        assert store.device(device_ids[1])["info"]["status"] == "ONLINE"
    finally:
        for release in releases:
            release()
        for stream in streams:
            await stream.aclose()
        assert not events.active
        store.close()


@pytest.mark.asyncio
async def test_support_bundle_is_available_through_authenticated_management_api(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    config_path = state / "config" / "gateway.json"
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_support_api_fixture",
            state_root=str(state),
            public_origin="http://127.0.0.1:8765",
            port=8765,
        ),
    )
    seeded = GatewayStore(state / "gateway.db")
    seeded.initialize(digest("owner"))
    seeded.close()
    app = create_app(state, gateway_config_path=config_path)
    credential, session = app.state.console_auth.create_session("owner_local")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8765",
            cookies={COOKIE: credential},
            headers={
                "X-CSRF-Token": session.csrf_token,
                "Origin": "http://127.0.0.1:8765",
            },
        ) as client:
            created = await client.post("/api/v1/management/support-bundles", json={})
            assert created.status_code == 200
            receipt = created.json()
            downloaded = await client.get(
                f"/api/v1/management/support-bundles/{receipt['id']}"
            )
            assert downloaded.status_code == 200
            assert downloaded.headers["cache-control"] == "no-store"
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                assert set(archive.namelist()) == {
                    "manifest.json",
                    "config.json",
                    "logs.json",
                }
            traversal = await client.get("/api/v1/management/support-bundles/not-a-bundle")
            assert traversal.status_code == 400
    finally:
        app.state.store.close()


def test_acceptance_fixture_is_loopback_owned_and_keeps_secrets_out_of_topology(
    tmp_path: Path,
) -> None:
    state = tmp_path / "fixture"
    credential = tmp_path / "private" / "management.json"
    receipt = prepare_fixture(state, credential, port=18877, device_count=50)
    topology = receipt.topology_file.read_text(encoding="utf-8")
    secret = credential.read_text(encoding="utf-8")
    assert receipt.device_count == 50
    assert '"synthetic_devices": 50' in topology
    assert "session_cookie" not in topology and "csrf_token" not in topology
    assert "session_cookie" in secret and "csrf_token" in secret
    store = GatewayStore(state / "gateway.db")
    try:
        assert store.db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 50
    finally:
        store.close()

    with pytest.raises(ValueError, match="loopback-only"):
        prepare_fixture(tmp_path / "bad", tmp_path / "bad-secret.json", host="192.0.2.10")
