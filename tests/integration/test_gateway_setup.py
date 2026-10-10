import json
from pathlib import Path

import pytest
from racp_domain.models import RACPError
from racp_gateway.config import GatewayConfig, write_gateway_config
from racp_gateway.console_auth import ConsoleAuth
from racp_gateway.local_admin import LocalAdminBroker, WindowsPrincipal
from racp_gateway.management.setup import SetupService
from racp_gateway.store import GatewayStore
from racp_protocol.management import ManagementPrincipal, SetupRequest
from racp_sdk.security import digest


def build(tmp_path: Path) -> tuple[SetupService, GatewayStore, Path]:
    root = tmp_path / "state"
    path = root / "config" / "gateway.json"
    write_gateway_config(
        path,
        GatewayConfig(
            instance_id="gateway_setup_test",
            mode="service",
            bind_address="127.0.0.1",
            port=18770,
            public_origin="http://127.0.0.1:18770",
            state_root=str(root.resolve()),
        ),
    )
    store = GatewayStore(root / "gateway.db")
    store.initialize(digest("owner"))
    auth = ConsoleAuth(store)
    return SetupService(path, store, auth), store, path


def request(port: int = 18771) -> SetupRequest:
    return SetupRequest(
        bind_address="127.0.0.1",
        port=port,
        public_origin=f"http://127.0.0.1:{port}",
    )


def test_bootstrap_is_five_minute_one_use_and_never_owner_token(tmp_path: Path) -> None:
    service, store, _ = build(tmp_path)
    try:
        broker = LocalAdminBroker(store)
        receipt = broker.issue_bootstrap(WindowsPrincipal("S-1-5-21-1", True, 1))
        exchange = service.exchange_bootstrap(receipt.bootstrap_code)
        assert exchange["expires_in_seconds"] == 300
        assert "owner" not in json.dumps(exchange).lower()
        with pytest.raises(PermissionError):
            service.exchange_bootstrap(receipt.bootstrap_code)
    finally:
        store.close()


def test_setup_preview_and_duplicate_commit_use_revision(tmp_path: Path) -> None:
    service, store, path = build(tmp_path)
    principal = ManagementPrincipal(actor_id="owner_local", role="owner", auth_revision=1)
    try:
        preview = service.preview(request())
        assert preview.valid and preview.restart_required
        changed = service.commit(request(), 1, principal)
        assert changed.revision == 2 and changed.restart_required
        with pytest.raises(RACPError) as conflict:
            service.commit(request(18772), 1, principal)
        assert conflict.value.error.code == "CONFLICT"
        assert json.loads(path.read_text(encoding="utf-8"))["revision"] == 2
    finally:
        store.close()


def test_setup_rejects_unauthorized_role_and_origin_tls_mismatch(tmp_path: Path) -> None:
    service, store, _ = build(tmp_path)
    try:
        invalid = SetupRequest(
            bind_address="0.0.0.0",
            port=18773,
            public_origin="http://example.test:18773",
        )
        check = service.preview(invalid)
        assert not check.valid
        viewer = ManagementPrincipal(actor_id="viewer", role="viewer", auth_revision=1)
        with pytest.raises(RACPError) as denied:
            service.commit(request(), 1, viewer)
        assert denied.value.error.code == "PERMISSION_DENIED"
    finally:
        store.close()
