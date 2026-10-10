"""Gateway management configuration is explicit, local and restart-safe."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from racp_gateway.config import (
    GatewayConfig,
    load_gateway_config,
    resolve_gateway_paths,
    validate_gateway_config,
)


def config_payload(state_root: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "instance_id": "gateway_test",
        "mode": "portable",
        "bind_address": "127.0.0.1",
        "port": 8765,
        "public_origin": "http://127.0.0.1:8765",
        "state_root": str(state_root),
        "tls": {},
        "auth": {},
        "retention": {},
        "update": {},
        "revision": 1,
    }


def test_config_rejects_unknown_fields_and_conflicting_tls(tmp_path: Path) -> None:
    payload = config_payload(tmp_path / "상태 폴더")
    with pytest.raises(ValidationError):
        GatewayConfig.model_validate({**payload, "unexpected": True})

    payload["tls"] = {"certificate_file": str(tmp_path / "server.pem")}
    config = GatewayConfig.model_validate(payload)
    check = validate_gateway_config(config)
    assert not check.valid
    assert "configured together" in " ".join(check.errors)


def test_paths_are_independent_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = (tmp_path / "상태 폴더").resolve()
    config = GatewayConfig.model_validate(config_payload(state))
    first = resolve_gateway_paths(config)
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    second = resolve_gateway_paths(config)
    assert first == second
    assert first.state_root == state
    assert first.database == state / "gateway.db"
    assert first.config_file == state / "config" / "gateway.json"


def test_existing_state_is_never_reinitialized(tmp_path: Path) -> None:
    state = (tmp_path / "host state").resolve()
    state.mkdir()
    marker = state / "gateway.db"
    marker.write_bytes(b"existing-state")
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(config_payload(state)), encoding="utf-8")

    loaded = load_gateway_config(path)
    assert loaded.state_root == str(state)
    assert marker.read_bytes() == b"existing-state"
    assert set(state.iterdir()) == {marker}


def test_update_feed_accepts_https_manifest_path_but_rejects_query(tmp_path: Path) -> None:
    payload = config_payload(tmp_path / "state")
    payload["update"] = {"feed_url": "https://updates.example/stable/gateway.json"}
    assert validate_gateway_config(GatewayConfig.model_validate(payload)).valid
    payload["update"] = {"feed_url": "https://updates.example/gateway.json?channel=stable"}
    check = validate_gateway_config(GatewayConfig.model_validate(payload))
    assert not check.valid
    assert any("update feed" in error for error in check.errors)


@pytest.mark.skipif(os.name != "nt", reason="Windows path contract")
def test_config_rejects_unc_state_root() -> None:
    payload = config_payload(Path("C:/RACP/state"))
    payload["state_root"] = r"\\server\share\racp"
    config = GatewayConfig.model_validate(payload)
    check = validate_gateway_config(config)
    assert not check.valid
    assert any("UNC" in error for error in check.errors)
