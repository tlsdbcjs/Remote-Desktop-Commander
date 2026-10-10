"""Windows native programs retain OS paths without inheriting application secrets."""

import json
import os
import subprocess
import sys

import pytest
from racp_agent.providers.shell import execution_env
from racp_domain.models import RACPError


@pytest.mark.skipif(os.name != "nt", reason="Windows native common-data folder")
def test_sanitized_environment_resolves_native_common_data_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from win32com.shell import shell, shellcon

    expected = shell.SHGetFolderPath(0, shellcon.CSIDL_COMMON_APPDATA, 0, 0)
    monkeypatch.setenv("ALLUSERSPROFILE", expected)
    monkeypatch.setenv("SYSTEMDRIVE", os.path.splitdrive(expected)[0])
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "owned-test-secret-must-not-be-inherited")
    code = (
        "import json,os; from win32com.shell import shell,shellcon; "
        "assert 'AWS_ACCESS_KEY_ID' not in os.environ; "
        "print(json.dumps(shell.SHGetFolderPath(0,shellcon.CSIDL_COMMON_APPDATA,0,0)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=execution_env({}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows OS environment protection")
@pytest.mark.parametrize("variable", ["SYSTEMDRIVE", "AllUsersProfile"])
def test_windows_system_paths_cannot_be_rebound(variable: str) -> None:
    with pytest.raises(RACPError) as error:
        execution_env({variable: "Z:/owned-spoof"})
    assert error.value.error.code == "PERMISSION_DENIED"


@pytest.mark.skipif(os.name != "nt", reason="Windows native OS path recovery")
def test_missing_parent_system_drive_is_recovered(monkeypatch: pytest.MonkeyPatch) -> None:
    from win32com.shell import shell, shellcon

    expected = shell.SHGetFolderPath(0, shellcon.CSIDL_COMMON_APPDATA, 0, 0)
    monkeypatch.delenv("SYSTEMDRIVE", raising=False)
    monkeypatch.delenv("ALLUSERSPROFILE", raising=False)
    code = (
        "import json; from win32com.shell import shell,shellcon; "
        "print(json.dumps(shell.SHGetFolderPath(0,shellcon.CSIDL_COMMON_APPDATA,0,0)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=execution_env({}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
