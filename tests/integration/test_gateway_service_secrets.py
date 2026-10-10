"""Service bootstrap credentials are created and consumed by the service identity."""

from pathlib import Path

import pytest
from racp_gateway.config import GatewayConfig, resolve_gateway_paths
from racp_gateway.local_admin import LocalAdminBroker, WindowsPrincipal
from racp_gateway.store import GatewayStore
from racp_gateway.windows_service import ensure_service_owner
from racp_sdk.security import SecretStore, digest


def service_config(root: Path) -> GatewayConfig:
    return GatewayConfig(
        instance_id="gateway_service_secrets",
        mode="service",
        bind_address="127.0.0.1",
        port=18766,
        public_origin="http://127.0.0.1:18766",
        state_root=str(root.resolve()),
    )


def test_non_admin_pipe_caller_denied(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    try:
        store.initialize(digest("owner"))
        broker = LocalAdminBroker(store)
        with pytest.raises(PermissionError, match="administrator"):
            broker.issue_bootstrap(WindowsPrincipal("S-1-5-21-100", False, 1))
        receipt = broker.issue_bootstrap(WindowsPrincipal("S-1-5-21-200", True, 1))
        assert 20 <= len(receipt.bootstrap_code) <= 128
        assert broker.consume_bootstrap(receipt.bootstrap_code) == "S-1-5-21-200"
        with pytest.raises(PermissionError, match="used"):
            broker.consume_bootstrap(receipt.bootstrap_code)
    finally:
        store.close()


def test_service_identity_can_decrypt_its_secret(tmp_path: Path) -> None:
    config = service_config(tmp_path / "service state")
    first = ensure_service_owner(config)
    paths = resolve_gateway_paths(config)
    assert SecretStore(paths.secrets / "owner.bin").load()["token"] == first
    assert ensure_service_owner(config) == first

    store = GatewayStore(paths.database)
    try:
        owner = store.db.execute("SELECT digest FROM owner WHERE id='owner_local'").fetchone()
        assert owner is not None and owner["digest"] == digest(first)
    finally:
        store.close()


def test_original_user_secret_cannot_be_silently_reused(tmp_path: Path) -> None:
    config = service_config(tmp_path / "service state")
    paths = resolve_gateway_paths(config)
    legacy = tmp_path / "portable-owner.bin"
    SecretStore(legacy).save({"token": "portable-owner"}, overwrite=False)
    store = GatewayStore(paths.database)
    try:
        store.initialize(digest("portable-owner"))
    finally:
        store.close()

    with pytest.raises(PermissionError, match="explicit migration"):
        ensure_service_owner(config)
    assert legacy.is_file()
    assert not (paths.secrets / "owner.bin").exists()
