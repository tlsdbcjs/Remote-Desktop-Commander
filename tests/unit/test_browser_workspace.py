import json
import os
import subprocess
from pathlib import Path

import pytest
from racp_agent.providers.browser_workspace import BrowserWorkspace
from racp_domain.models import RACPError


def test_private_browser_workspace_collects_only_proven_dead_ownership(tmp_path: Path) -> None:
    store = BrowserWorkspace(tmp_path / "browsers")
    directory = store.create("browser_" + "a" * 32)
    (directory / "temp" / "cache").write_bytes(b"owned")
    assert store.collect() == {"removed": 0, "live": 1, "unverified": 0}
    marker = directory / "ownership.json"
    record = json.loads(marker.read_text(encoding="utf-8"))
    record.update(worker_pid=record["owner_pid"], worker_birth=record["owner_birth"], owner_birth=0)
    marker.write_text(json.dumps(record), encoding="utf-8")
    assert store.collect()["live"] == 1  # A live worker cannot lose its files.
    record.update(worker_birth=0)
    marker.write_text(json.dumps(record), encoding="utf-8")
    assert store.collect()["removed"] == 1 and not directory.exists()
    unverified = store.root / ("browser_" + "b" * 32)
    unverified.mkdir()
    assert store.collect()["unverified"] == 1 and unverified.exists()
    for path in (store.root, tmp_path, tmp_path / ("browser_" + "c" * 32)):
        with pytest.raises(RACPError):
            store.remove(path)
    if os.name != "nt":
        assert store.root.stat().st_mode & 0o077 == 0
    else:
        import win32security

        security = win32security.GetNamedSecurityInfo(
            str(store.root), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        acl = security.GetSecurityDescriptorDacl()
        assert acl.GetAceCount() == 2
        assert security.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED


def test_browser_removal_never_follows_external_reparse_target(tmp_path: Path) -> None:
    store = BrowserWorkspace(tmp_path / "browsers")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("retain", encoding="utf-8")
    directory = store.create("browser_" + "d" * 32)
    link = directory / "nested-link"
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        assert completed.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    assert store.usage(directory) < sentinel.stat().st_size + 4096
    store.remove(directory)
    assert sentinel.read_text(encoding="utf-8") == "retain"
