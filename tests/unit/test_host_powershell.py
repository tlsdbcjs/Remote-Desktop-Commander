"""PowerShell entry points preserve argument boundaries and native exit codes."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "scripts/host"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell required")


def test_scripts_parse_on_windows_powershell_without_changing_execution_policy() -> None:
    scripts = [
        HOST / name
        for name in (
            "Start-Gateway.ps1",
            "Status-Gateway.ps1",
            "Create-Connection-File.ps1",
            "Create-Console-Login.ps1",
            "Invoke-RacpHost.ps1",
        )
    ]
    assert all(path.is_file() for path in scripts), "PowerShell source entry points missing"
    for path in scripts:
        code = (
            "$tokens=$null; $parseErrors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{path.as_posix()}',[ref]$tokens,[ref]$parseErrors) | Out-Null; "
            "if ($parseErrors.Count) { $parseErrors | Out-String | Write-Error; exit 1 }"
        )
        result = subprocess.run([POWERSHELL, "-NoProfile", "-Command", code], capture_output=True)
        assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.parametrize("packaged", [False, True])
def test_launcher_forwards_space_unicode_and_literal_arguments_and_exit_code(
    tmp_path: Path,
    packaged: bool,
) -> None:
    assert HOST.is_dir(), "PowerShell source entry points missing"
    root = tmp_path / "Gateway 공백 폴더"
    launchers = root if packaged else root / "scripts/host"
    launchers.mkdir(parents=True)
    for path in HOST.glob("*.ps1"):
        shutil.copy2(path, launchers / path.name)
    runtime = root / "runtime"
    shutil.copytree(
        Path(sys.base_prefix),
        runtime,
        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc"),
    )
    if not packaged:
        (root / "pyproject.toml").write_text("[project]\n")
        # Runtime remains local to this fixture; the source launcher also accepts it.
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    fixture = "import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=True))\nsys.exit(7)\n"
    for filename in ["gateway_host.py", "create_connection_file.py", "create_console_login.py"]:
        (scripts / filename).write_text(fixture)
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "RemoteSigned",
            "-File",
            str(launchers / "Create-Connection-File.ps1"),
            "-Name",
            "PC 한글 & literal",
            "-NoOpen",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
            timeout=60,
    )
    assert result.returncode == 7, result.stderr
    arguments = json.loads(result.stdout.strip())
    assert arguments[arguments.index("--name") + 1] == "PC 한글 & literal"
    assert "--no-open" in arguments
    assert (arguments[0] == "connection") is packaged
