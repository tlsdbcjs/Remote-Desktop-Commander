import importlib.util
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "apps/client/build/maintenance.py"
SPEC = importlib.util.spec_from_file_location("client_maintenance", SOURCE)
assert SPEC and SPEC.loader
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


def test_verified_backup_preserves_bytes_and_excludes_old_backups_and_locks(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    (root / "credential.bin").write_bytes(b"protected-credential-fixture")
    (root / "backups").mkdir()
    (root / "backups/old.txt").write_text("old backup")
    (root / "agent.lock").write_bytes(b"0")
    output = maintenance.backup_state(root)
    assert output is not None
    assert (output / "credential.bin").read_bytes() == (root / "credential.bin").read_bytes()
    manifest = json.loads((output / "backup-manifest.json").read_text())
    assert [item["file"] for item in manifest["files"]] == ["credential.bin"]
    assert not (output / "agent.lock").exists() and not (output / "backups").exists()


async def test_unconfirmed_agent_stop_aborts_before_backup_and_registry_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(maintenance, "no_gui_running", lambda *args: None)

    async def incomplete(*args: object) -> dict[str, str]:
        return {"state": "STOPPED", "cleanup_status": "unknown"}

    monkeypatch.setattr(maintenance, "stop", incomplete)
    with pytest.raises(RuntimeError, match="not confirmed"):
        await maintenance.prepare(
            tmp_path, tmp_path / "state", "RACP Client.exe", "uninstall", "fixture"
        )
    assert not (tmp_path / "state").exists()


def test_installer_gui_guard_matches_the_exact_installation_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "RACP Fixture.exe"
    other = SimpleNamespace(info={"name": name}, exe=lambda: str(tmp_path / "other" / name))
    own = SimpleNamespace(info={"name": name}, exe=lambda: str(tmp_path / "program" / name))
    monkeypatch.setattr(maintenance.psutil, "process_iter", lambda *args: [other])
    maintenance.no_gui_running(tmp_path / "program", name)
    monkeypatch.setattr(maintenance.psutil, "process_iter", lambda *args: [other, own])
    with pytest.raises(RuntimeError, match="full exit"):
        maintenance.no_gui_running(tmp_path / "program", name)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registry test")
def test_uninstall_does_not_remove_another_installations_startup_value(tmp_path: Path) -> None:
    import winreg

    name = "RACP-Maintenance-Acceptance-" + uuid.uuid4().hex
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    target = tmp_path / "Fixture.exe"
    foreign = '"' + str(tmp_path / "other/Fixture.exe") + '" racp-background-agent'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, foreign)
    try:
        with pytest.raises(RuntimeError, match="different installation"):
            maintenance.remove_own_login(name, target)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            assert winreg.QueryValueEx(key, name)[0] == foreign
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(
                key, name, 0, winreg.REG_SZ, '"' + str(target) + '" racp-background-agent'
            )
        assert maintenance.remove_own_login(name, target)
        assert not maintenance.remove_own_login(name, target)
    finally:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
