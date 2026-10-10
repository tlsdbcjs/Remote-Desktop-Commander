import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from racp_gateway.app import create_app
from racp_gateway.config import GatewayConfig, write_gateway_config
from racp_gateway.console_auth import COOKIE
from racp_gateway.store import GatewayStore
from racp_sdk.security import digest


def build(tmp_path: Path) -> tuple[object, str, str, Path]:
    root = tmp_path / "gateway state"
    config_path = root / "config" / "gateway.json"
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_management_test",
            state_root=str(root.resolve()),
            public_origin="http://127.0.0.1:8765",
            port=8765,
        ),
    )
    seeded = GatewayStore(root / "gateway.db")
    seeded.initialize(digest("owner-secret-for-management-tests"))
    seeded.close()
    app = create_app(root, gateway_config_path=config_path)
    credential, session = app.state.console_auth.create_session("owner_local")
    return app, credential, session.csrf_token, config_path


def client_for(app: object, credential: str, csrf: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
        cookies={COOKIE: credential},
        headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1:8765"},
    )


@pytest.mark.asyncio
async def test_management_status_uses_ssot_version_and_separates_health_from_readiness(
    tmp_path: Path,
) -> None:
    app, credential, csrf, _ = build(tmp_path)
    try:
        async with client_for(app, credential, csrf) as client:
            health = await client.get("/healthz")
            status = await client.get("/api/v1/management/status")
        assert health.status_code == 200 and health.json() == {"status": "ok"}
        assert status.status_code == 200
        body = status.json()
        assert body["ready"] is True
        assert body["instance_id"] == "gateway_management_test"
        assert body["mode"] == "portable"
        assert body["version"] != "0.1.0"
        assert body["database"] == "ready"
    finally:
        app.state.store.close()


@pytest.mark.asyncio
async def test_management_settings_stage_checks_revision_and_never_echoes_secret_paths(
    tmp_path: Path,
) -> None:
    app, credential, csrf, config_path = build(tmp_path)
    try:
        async with client_for(app, credential, csrf) as client:
            current = await client.get("/api/v1/management/settings")
            assert current.status_code == 200
            assert current.headers["etag"] == '"1"'
            assert current.headers["x-racp-revision"] == "1"
            encoded = json.dumps(current.json()).lower()
            assert "private_key_file" not in encoded
            assert "oauth_config_file" not in encoded

            staged = await client.post(
                "/api/v1/management/settings/stage",
                json={"expected_revision": 1, "changes": {"port": 18776}},
            )
            assert staged.status_code == 200
            assert staged.json()["restart_required"] is True

            stale_etag = await client.post(
                "/api/v1/management/settings/stage",
                headers={"If-Match": '"2"'},
                json={"expected_revision": 1, "changes": {"port": 18776}},
            )
            assert stale_etag.status_code == 409

            bad_revision = await client.post(
                "/api/v1/management/settings/stage",
                json={"expected_revision": 2, "changes": {"port": 18777}},
            )
            assert bad_revision.status_code == 409

            secret = await client.post(
                "/api/v1/management/settings/stage",
                json={
                    "expected_revision": 1,
                    "changes": {"update": {"trust_key_file": "C:/secret.pem"}},
                },
            )
            assert secret.status_code == 400
    finally:
        app.state.store.close()
    assert json.loads(config_path.read_text(encoding="utf-8"))["port"] == 8765


@pytest.mark.asyncio
async def test_management_settings_put_commits_once_with_revision_and_etag(tmp_path: Path) -> None:
    app, credential, csrf, config_path = build(tmp_path)
    try:
        async with client_for(app, credential, csrf) as client:
            committed = await client.put(
                "/api/v1/management/settings",
                headers={"If-Match": '"1"'},
                json={"expected_revision": 1, "changes": {"port": 18776}},
            )
            assert committed.status_code == 200
            assert committed.headers["etag"] == '"2"'
            assert committed.headers["x-racp-revision"] == "2"
            assert committed.json()["revision"] == 2
            assert committed.json()["values"]["port"] == 18776
            assert committed.json()["restart_required"] is True

            stale = await client.put(
                "/api/v1/management/settings",
                headers={"If-Match": '"1"'},
                json={"expected_revision": 1, "changes": {"port": 18777}},
            )
            assert stale.status_code == 409

            status = await client.get("/api/v1/management/status")
            assert status.status_code == 200
            assert status.json()["settings_revision"] == 2
    finally:
        app.state.store.close()
    document = json.loads(config_path.read_text(encoding="utf-8"))
    assert document["revision"] == 2
    assert document["port"] == 18776


@pytest.mark.asyncio
async def test_management_restore_stages_pre_restore_backup_and_host_handoff(
    tmp_path: Path,
) -> None:
    app, credential, csrf, _ = build(tmp_path)
    try:
        enrolled = app.state.store.enroll(app.state.store.enrollment("restore-agent"))
        async with client_for(app, credential, csrf) as client:
            backup = await client.post(
                "/api/v1/management/backups",
                json={"idempotency_key": "restore-source"},
            )
            assert backup.status_code == 200
            backup_id = backup.json()["id"]
            requested = await client.post(
                "/api/v1/management/restores",
                json={
                    "backup_id": backup_id,
                    "expected_instance_id": "gateway_management_test",
                    "expected_revision": 1,
                    "idempotency_key": "restore-apply-once",
                    "confirm": True,
                },
            )
            assert requested.status_code == 200
            job = requested.json()
            assert job["kind"] == "restore"
            assert job["state"] == "DEFERRED"
            assert job["actor_id"] == "owner_local"
            detail = await client.get(f"/api/v1/management/maintenance-jobs/{job['id']}")
            assert detail.status_code == 200
            assert detail.json()["state"] == "DEFERRED"

        stage = tmp_path / "gateway state" / "restores" / job["id"]
        assert (stage / "handoff.json").is_file()
        staged = sqlite3.connect(stage / "gateway.db")
        try:
            credential = staged.execute(
                "SELECT expires FROM credentials WHERE device_id=?",
                (enrolled["device_id"],),
            ).fetchone()
            assert credential is not None and credential[0] == 0
            status = json.loads(
                staged.execute(
                    "SELECT info FROM devices WHERE id=?",
                    (enrolled["device_id"],),
                ).fetchone()[0]
            )["status"]
            assert status == "OFFLINE"
        finally:
            staged.close()
    finally:
        app.state.store.close()


@pytest.mark.asyncio
async def test_management_mutation_requires_same_origin_and_valid_csrf(tmp_path: Path) -> None:
    app, credential, csrf, _ = build(tmp_path)
    try:
        async with client_for(app, credential, csrf) as client:
            client.headers["Origin"] = "http://evil.invalid"
            denied_origin = await client.post(
                "/api/v1/management/settings/stage",
                json={"expected_revision": 1, "changes": {"port": 18778}},
            )
            assert denied_origin.status_code == 403
            client.headers["Origin"] = "http://127.0.0.1:8765"
            client.headers["X-CSRF-Token"] = "invalid"
            denied_csrf = await client.post(
                "/api/v1/management/settings/stage",
                json={"expected_revision": 1, "changes": {"port": 18778}},
            )
            assert denied_csrf.status_code == 403
    finally:
        app.state.store.close()


@pytest.mark.asyncio
async def test_management_user_and_group_mutation_routes_reuse_scoped_services(
    tmp_path: Path,
) -> None:
    app, credential, csrf, _ = build(tmp_path)
    try:
        enrolled = app.state.store.enroll(app.state.store.enrollment("managed-agent"))
        device_id = enrolled["device_id"]
        async with client_for(app, credential, csrf) as client:
            created_user = await client.post(
                "/api/v1/management/users",
                json={
                    "issuer": "https://idp.example",
                    "subject": "operator@example",
                    "display_name": "Operator",
                    "role": "viewer",
                },
            )
            assert created_user.status_code == 200
            user_id = created_user.json()["id"]
            patched_user = await client.patch(
                f"/api/v1/management/users/{user_id}",
                json={"role": "operator", "device_grants": [device_id]},
            )
            assert patched_user.status_code == 200
            assert patched_user.json()["role"] == "operator"
            assert patched_user.json()["device_grants"] == [device_id]
            assert patched_user.json()["auth_revision"] == 2

            created_group = await client.post(
                "/api/v1/management/groups",
                json={"name": "lab", "tags": ["windows"]},
            )
            assert created_group.status_code == 200
            group_id = created_group.json()["id"]
            assigned = await client.patch(
                f"/api/v1/management/devices/{device_id}/groups",
                json={"group_ids": [group_id], "expected_revision": 1},
            )
            assert assigned.status_code == 200
            assert assigned.json()["group_ids"] == [group_id]
            assert assigned.json()["revision"] == 2

            changed_group = await client.patch(
                f"/api/v1/management/groups/{group_id}",
                json={
                    "expected_revision": 1,
                    "name": "lab-renamed",
                    "tags": ["windows", "trusted"],
                    "device_ids": [device_id],
                },
            )
            assert changed_group.status_code == 200
            assert changed_group.json()["revision"] == 2
            assert changed_group.json()["device_ids"] == [device_id]

            groups = await client.get("/api/v1/management/groups")
            assert groups.status_code == 200
            assert [group["name"] for group in groups.json()] == ["lab-renamed"]

            admin = await client.post(
                "/api/v1/management/users",
                json={
                    "issuer": "https://idp.example",
                    "subject": "admin@example",
                    "display_name": "Admin",
                    "role": "admin",
                },
            )
            assert admin.status_code == 200
        admin_credential, admin_session = app.state.console_auth.create_session(admin.json()["id"])
        async with client_for(app, admin_credential, admin_session.csrf_token) as admin_client:
            denied = await admin_client.post(
                "/api/v1/management/users",
                json={
                    "issuer": "https://idp.example",
                    "subject": "blocked@example",
                    "display_name": "Blocked",
                    "role": "viewer",
                },
            )
            assert denied.status_code == 403
    finally:
        app.state.store.close()
