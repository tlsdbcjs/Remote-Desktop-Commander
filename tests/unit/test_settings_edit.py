from pathlib import Path

import pytest
from racp_agent.connect import credential_document
from racp_agent.desktop_control import SettingsUpdate, execute, information
from racp_agent.main import open_agent
from racp_agent.settings import AgentSettings, saved_settings
from racp_agent.settings_edit import editable_settings
from racp_agent.workspaces import WorkspaceSpec
from racp_domain.models import RACPError
from racp_sdk.security import SecretStore


def configured(root: Path) -> Path:
    workspace = root / "old-workspace"
    workspace.mkdir()
    credentials = root / "agent/credential.bin"
    settings = AgentSettings(
        gateway="https://gateway.example",
        device_id="dev_edit_fixture",
        workspace=workspace,
        data_dir=root / "agent/data",
        profile="standard",
    )
    SecretStore(credentials).save(
        credential_document(settings, "test-device-credential-never-return")
    )
    settings.data_dir.mkdir()
    (settings.data_dir / "retained-journal.txt").write_text("retained")
    return credentials


def change(
    credentials: Path,
    workspace: Path,
    gateway: str = "https://gateway.example",
) -> SettingsUpdate:
    return SettingsUpdate(
        action="update_settings",
        revision=editable_settings(credentials)["revision"],
        gateway=gateway,
        workspace=workspace,
        profile="read_only",
    )


async def test_repair_missing_workspace_preserves_device_secret_journal_and_backup(
    tmp_path: Path,
) -> None:
    credentials = configured(tmp_path)
    previous = SecretStore(credentials).load()
    (tmp_path / "old-workspace").rmdir()
    with pytest.raises((ValueError, OSError)):
        information(credentials)
    preview = editable_settings(credentials)
    assert "credential" not in preview and "agent_settings" not in preview
    assert "test-device-credential-never-return" not in str(preview)
    result = await execute(change(credentials, tmp_path), credentials.parent)
    current = SecretStore(credentials).load()
    assert result["device_id"] == previous["device_id"]
    assert current["credential"] == previous["credential"]
    settings = saved_settings(current)
    assert settings and settings.workspace == tmp_path and settings.profile == "read_only"
    assert (settings.data_dir / "retained-journal.txt").read_text() == "retained"
    backups = list((credentials.parent / "settings-backups").glob("*.bin"))
    assert len(backups) == 1 and SecretStore(backups[0]).load() == previous
    with open_agent(credentials) as agent:
        assert agent.device_id == previous["device_id"]
        assert agent.profile == "read_only"
        assert agent.filesystem.guard.inventory()[0]["path"] == str(tmp_path)


async def test_running_agent_and_stale_editor_cannot_overwrite_settings(tmp_path: Path) -> None:
    credentials = configured(tmp_path)
    request = change(credentials, tmp_path)
    original = credentials.read_bytes()
    with open_agent(credentials):
        with pytest.raises(RACPError) as raised:
            await execute(request, credentials.parent)
        assert raised.value.error.code == "SETTINGS_BUSY"
        assert credentials.read_bytes() == original
    await execute(request, credentials.parent)
    updated = credentials.read_bytes()
    with pytest.raises(RACPError) as raised:
        await execute(request, credentials.parent)
    assert raised.value.error.code == "SETTINGS_CHANGED"
    assert credentials.read_bytes() == updated


async def test_gateway_address_can_change_without_reenrolling_device(tmp_path: Path) -> None:
    credentials = configured(tmp_path)
    previous = SecretStore(credentials).load()
    result = await execute(
        change(credentials, tmp_path / "old-workspace", "https://gateway.example:9443"),
        credentials.parent,
    )
    current = SecretStore(credentials).load()
    settings = saved_settings(current)
    assert settings
    assert result["gateway"] == "https://gateway.example:9443"
    assert settings.gateway == "https://gateway.example:9443"
    assert current["gateway"] == "https://gateway.example:9443"
    assert current["device_id"] == previous["device_id"]
    assert current["credential"] == previous["credential"]


async def test_invalid_gateway_edit_preserves_original(tmp_path: Path) -> None:
    credentials = configured(tmp_path)
    original = credentials.read_bytes()
    request = change(credentials, tmp_path / "old-workspace", "http://gateway.example")
    with pytest.raises(RACPError) as raised:
        await execute(request, credentials.parent)
    assert raised.value.error.code == "GATEWAY_INVALID"
    assert credentials.read_bytes() == original


async def test_invalid_repair_and_backup_failure_preserve_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = configured(tmp_path)
    original = credentials.read_bytes()
    request = change(credentials, tmp_path / "missing")
    with pytest.raises(RACPError) as raised:
        await execute(request, credentials.parent)
    assert raised.value.error.code == "WORKSPACE_INVALID"
    assert credentials.read_bytes() == original
    request.workspace = tmp_path
    request.ca_file = tmp_path / "missing.pem"
    with pytest.raises(RACPError) as raised:
        await execute(request, credentials.parent)
    assert raised.value.error.code == "CA_INVALID"
    assert credentials.read_bytes() == original
    request.ca_file = None

    def fail_save(*args: object, **kwargs: object) -> None:
        raise PermissionError("Cannot create protected backup")

    monkeypatch.setattr(SecretStore, "save", fail_save)
    with pytest.raises(PermissionError):
        await execute(request, credentials.parent)
    assert credentials.read_bytes() == original


def test_unreadable_credential_never_offers_editable_settings(tmp_path: Path) -> None:
    credentials = configured(tmp_path)
    original = b"corrupt-protected-credential"
    credentials.write_bytes(original)
    with pytest.raises((ValueError, PermissionError)):
        editable_settings(credentials)
    assert credentials.read_bytes() == original


async def test_desktop_setting_preserves_device_and_is_used_by_agent(tmp_path: Path) -> None:
    import os

    credentials = configured(tmp_path)
    original = SecretStore(credentials).load()
    request = change(credentials, tmp_path)
    request.desktop_enabled = True
    await execute(request, credentials.parent)
    assert information(credentials)["desktop_enabled"] is True
    assert SecretStore(credentials).load()["credential"] == original["credential"]
    if os.name == "nt":
        from racp_agent.broker.identity import process_identity

        session = process_identity(os.getpid()).session
        with open_agent(credentials) as agent:
            assert session == 0 or session in agent.desktop.sessions
    # An older client's omitted field must preserve the saved opt-in.
    await execute(change(credentials, tmp_path), credentials.parent)
    assert editable_settings(credentials)["desktop_enabled"] is True


async def test_repair_missing_additional_folder_and_ca(tmp_path: Path) -> None:
    credentials = configured(tmp_path)
    old = SecretStore(credentials).load()
    settings = saved_settings(old)
    assert settings
    extra = tmp_path / "extra"
    extra.mkdir()
    settings.allowed_workspaces = [WorkspaceSpec(id="extra", path=extra)]
    # Stored document can still be inspected when external resources disappear.
    settings.ca_file = tmp_path / "missing-ca.pem"
    SecretStore(credentials).save(credential_document(settings, old["credential"]))
    extra.rmdir()
    with pytest.raises((ValueError, OSError)):
        information(credentials)
    preview = editable_settings(credentials)
    assert preview["allowed_workspaces"][0]["id"] == "extra"
    request = change(credentials, tmp_path)
    await execute(request, credentials.parent)
    assert information(credentials)["allowed_workspaces"] == []
    assert SecretStore(credentials).load()["credential"] == old["credential"]
