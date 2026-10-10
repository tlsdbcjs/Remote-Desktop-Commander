"""Protected local settings migrate without reenrollment or permission expansion."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from racp_agent.connect import credential_document, prepare
from racp_agent.desktop_control import (
    REQUEST,
    Enrollment,
    SettingsUpdate,
    execute,
    failure_code,
    information,
)
from racp_agent.main import open_agent
from racp_agent.settings import saved_settings, stored_settings
from racp_agent.settings_edit import editable_settings
from racp_policy.engine import Decision
from racp_policy.permissions import LocalPermissions
from racp_protocol.permissions import permission_catalog
from racp_sdk.security import SecretStore


def test_clipboard_only_grant_starts_broker_without_enabling_desktop_input(tmp_path: Path) -> None:
    _, settings = prepare(
        "https://gateway.example",
        tmp_path,
        tmp_path / "clip-state",
        permissions=LocalPermissions(grants={"clipboard.text.read": "allow"}),
    )
    assert settings.desktop_enabled is False
    assert settings.session_broker_enabled is True
    disabled = settings.model_copy(
        update={
            "permissions": LocalPermissions(
                grants={"clipboard.text.read": "allow"}, disabled_categories=("clipboard",)
            )
        }
    )
    assert disabled.session_broker_enabled is False


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows interactive Broker")
def test_clipboard_only_saved_settings_start_the_current_session_broker(tmp_path: Path) -> None:
    import os

    from racp_agent.broker.identity import process_identity

    path, settings = prepare(
        "https://gateway.example",
        tmp_path,
        tmp_path / "clip-start",
        permissions=LocalPermissions(grants={"clipboard.text.read": "allow"}),
    )
    SecretStore(path).save(credential_document(settings, "own-clip-start-fixture"))
    with open_agent(path) as agent:
        expected = process_identity(os.getpid()).session
        assert set(agent.desktop.sessions) == ({expected} if expected else set())
        assert agent.permissions.leaf("desktop.keyboard.keys") == Decision.DENY


def legacy_document(root: Path, enabled: bool) -> dict[str, str]:
    settings = {
        "version": 1,
        "gateway": "https://gateway.example",
        "device_id": "dev_migrate",
        "workspace": str(root),
        "data_dir": str(root / "data"),
        "profile": "standard",
        "desktop_enabled": enabled,
    }
    return {
        "gateway": settings["gateway"],
        "device_id": settings["device_id"],
        "credential": "protected-migration-fixture-secret",
        "agent_settings": json.dumps(settings),
    }


@pytest.mark.parametrize("enabled", [False, True])
def test_v1_migration_preserves_identity_and_desktop_ceiling(tmp_path: Path, enabled: bool) -> None:
    source = legacy_document(tmp_path, enabled)
    settings = saved_settings(source)
    assert settings and settings.version == 2
    assert settings.desktop_enabled is enabled
    assert settings.permissions.grants["desktop.keyboard.keys"] == ("allow" if enabled else "deny")
    assert settings.device_id == source["device_id"]
    assert json.loads(source["agent_settings"])["version"] == 1  # read is non-mutating


def test_v2_requires_explicit_permissions_and_rejects_managed_or_future_fields(
    tmp_path: Path,
) -> None:
    source = legacy_document(tmp_path, False)
    document = json.loads(source["agent_settings"])
    for mutation in (
        {"version": 2},
        {"version": 3},
        {"version": 1, "permissions": {}},
        {"managed_policy": {}},
    ):
        with pytest.raises(ValidationError):
            stored_settings({**source, "agent_settings": json.dumps({**document, **mutation})})


def test_enrollment_explicit_empty_and_selected_permissions_are_not_replaced(
    tmp_path: Path,
) -> None:
    for index, permissions in enumerate(
        (LocalPermissions(), LocalPermissions(grants={"files.list": "allow"}))
    ):
        _, settings = prepare(
            "https://gateway.example", tmp_path, tmp_path / str(index), permissions=permissions
        )
        assert settings.version == 2 and settings.permissions == permissions
        assert saved_settings(credential_document(settings, "protected-fixture-secret")) == settings
    for action in ("enroll", "enroll_connection"):
        raw = {
            "action": action,
            "workspace": str(tmp_path),
            "permissions": {"grants": {"files.list": "allow"}},
        }
        if action == "enroll":
            raw.update(gateway="https://gateway.example", token="protected-enrollment-fixture")
            value = Enrollment.model_validate_json(json.dumps(raw))
        else:
            from racp_agent.desktop_control import ImportConnection

            raw.update(path=str(tmp_path / "connection.json"), file_sha256="a" * 64)
            value = ImportConnection.model_validate_json(json.dumps(raw))
        assert value.permissions and value.permissions.grants == {"files.list": "allow"}


async def test_permission_edit_preserves_secret_and_restart_enforces_snapshot(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "state/credential.bin"
    source = legacy_document(tmp_path, False)
    SecretStore(credentials).save(source)
    preview = editable_settings(credentials)
    assert preview["permissions"]["grants"]["files.read.text"] == "allow"
    request = SettingsUpdate(
        action="update_settings",
        revision=preview["revision"],
        gateway=source["gateway"],
        workspace=tmp_path,
        profile="trusted_personal",
        permissions=LocalPermissions(grants={"files.list": "allow"}),
    )
    result = await execute(request, credentials.parent)
    assert result["permissions"]["grants"] == {"files.list": "allow"}
    assert SecretStore(credentials).load()["credential"] == source["credential"]
    with open_agent(credentials) as agent:
        assert agent.permissions.decision("filesystem.read", {}) == Decision.DENY
        assert agent.permissions.decision("filesystem.list", {}) == Decision.ALLOW
    assert information(credentials)["permissions"] == result["permissions"]
    # Omission by an older editor preserves the current ceiling.
    await execute(
        SettingsUpdate(
            action="update_settings",
            revision=editable_settings(credentials)["revision"],
            gateway=source["gateway"],
            workspace=tmp_path,
            profile="read_only",
        ),
        credentials.parent,
    )
    assert saved_settings(SecretStore(credentials).load()).permissions.grants == {
        "files.list": "allow"
    }


def test_full_catalog_fits_and_oversized_constraints_fail_before_enrollment(tmp_path: Path) -> None:
    permissions = LocalPermissions(grants={item.id: "deny" for item in permission_catalog()})
    _, settings = prepare(
        "https://gateway.example", tmp_path, tmp_path / "fits", permissions=permissions
    )
    assert len(settings.model_dump_json().encode()) < 16384
    inflated = permissions.model_dump(mode="json")
    inflated["constraints"]["executable_allowlist"] = ["C:/" + "x" * 4000 for _ in range(128)]
    oversized = LocalPermissions.model_validate_json(json.dumps(inflated))
    with pytest.raises(ValueError, match="allowance"):
        prepare("https://gateway.example", tmp_path, tmp_path / "too-big", permissions=oversized)
    assert not (tmp_path / "too-big").exists()


def test_permission_validation_has_a_specific_safe_client_diagnostic(tmp_path: Path) -> None:
    raw = {
        "action": "enroll",
        "gateway": "https://gateway.example",
        "workspace": str(tmp_path),
        "token": "private-one-use-fixture-token",
        "permissions": {"grants": {"storage.format": "allow"}},
    }
    with pytest.raises(ValidationError) as raised:
        REQUEST.validate_json(json.dumps(raw))
    assert failure_code(raised.value, None) == "PERMISSIONS_INVALID"


@pytest.mark.parametrize("version", [True, 1.0, 2.0])
def test_settings_version_does_not_coerce_boolean_or_float_into_migration(
    tmp_path: Path, version: object
) -> None:
    source = legacy_document(tmp_path, False)
    document = json.loads(source["agent_settings"])
    document["version"] = version
    if version == 2:
        document["permissions"] = {"grants": {"files.list": "allow"}}
    with pytest.raises(ValidationError):
        stored_settings({**source, "agent_settings": json.dumps(document)})


@pytest.mark.parametrize("version", [True, 1.0])
def test_permission_schema_version_requires_an_integer(version: object) -> None:
    with pytest.raises(ValidationError):
        LocalPermissions.model_validate_json(json.dumps({"version": version}))
