import json
import zipfile
from pathlib import Path

import pytest
from racp_gateway.config import GatewayConfig, write_gateway_config
from racp_gateway.management.logs import LogStore
from racp_gateway.store import GatewayStore
from racp_gateway.support_bundle import SupportBundleRequest, create_support_bundle
from racp_protocol.management import SafeLogEntry
from racp_sdk.security import digest


def test_support_bundle_excludes_secrets_database_and_user_paths(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = GatewayStore(state / "gateway.db")
    store.initialize(digest("owner-secret"))
    config_path = state / "config" / "gateway.json"
    secret_path = tmp_path / "Users" / "person" / "private-key.pem"
    secret_path.parent.mkdir(parents=True)
    secret_path.write_text("test public key fixture", encoding="utf-8")
    write_gateway_config(
        config_path,
        GatewayConfig(
            instance_id="gateway_support_test",
            state_root=str(state),
            update={
                "trust_key_file": str(secret_path),
            },
        ),
    )
    LogStore(store).append(
        SafeLogEntry(
            timestamp="2026-10-07T00:00:00Z",
            level="ERROR",
            event="request_failed",
            message="Authorization: Bearer super-secret-token",
            fields={
                "authorization": "Bearer super-secret-token",
                "cookie": "session=super-secret-cookie",
                "path": str(tmp_path / "Users" / "person" / "document.txt"),
                "safe_code": "E_TEST",
            },
        )
    )
    try:
        receipt = create_support_bundle(
            SupportBundleRequest(
                store=store,
                output_dir=tmp_path / "support",
                config_path=config_path,
            )
        )
        bundle = tmp_path / "support" / receipt.file_name
        with zipfile.ZipFile(bundle) as archive:
            assert set(archive.namelist()) == {
                "manifest.json",
                "config.json",
                "logs.json",
            }
            combined = b"".join(archive.read(name) for name in archive.namelist()).decode()
            config = json.loads(archive.read("config.json"))
        assert "super-secret-token" not in combined
        assert "super-secret-cookie" not in combined
        assert str(secret_path) not in combined
        assert "gateway.db" not in archive.namelist()
        assert config["tls"] == {
            "configured": False,
            "client_ca_configured": False,
        }
        assert config["update"]["trust_key_configured"] is True
    finally:
        store.close()


def test_support_bundle_enforces_size_limit(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    try:
        with pytest.raises(ValueError):
            create_support_bundle(
                SupportBundleRequest(
                    store=store,
                    output_dir=tmp_path / "support",
                    max_bytes=1024,
                )
            )
    finally:
        store.close()


def test_support_bundle_caps_log_count_and_does_not_leave_partial_archive(tmp_path: Path) -> None:
    store = GatewayStore(tmp_path / "gateway.db")
    store.initialize(digest("owner"))
    output = tmp_path / "support"
    try:
        logs = LogStore(store)
        for index in range(550):
            logs.append(
                SafeLogEntry(
                    timestamp="2026-10-07T00:00:00Z",
                    level="INFO",
                    event="bounded_log",
                    message="safe",
                    fields={"count": index},
                )
            )
        receipt = create_support_bundle(
            SupportBundleRequest(store=store, output_dir=output, log_limit=999)
        )
        assert receipt.log_entries == 500
        assert receipt.size_bytes <= 5 * 1024 * 1024
        assert not list(output.glob(".*.zip"))
    finally:
        store.close()
