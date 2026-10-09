import json
import subprocess
import sys
import tomllib
from pathlib import Path

from scripts.build_gateway import PackagingConfig, package_gateway

ROOT = Path(__file__).resolve().parents[2]


def test_gateway_build_dry_run_defines_setup_and_portable_without_publishing() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_gateway.py",
            "--platform",
            "win",
            "--arch",
            "x64",
            "--targets",
            "setup,portable",
            "--node",
            "unused-in-dry-run.exe",
            "--dry-run",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    plan = json.loads(result.stdout)
    assert plan["version"]
    assert plan["platform"] == "win-x64"
    assert plan["targets"] == ["setup", "portable"]
    assert plan["setup_engine"] == "nsis"
    assert plan["publish"] is False


def test_package_gateway_exposes_typed_dry_run_contract() -> None:
    plan = package_gateway(
        PackagingConfig(
            node=Path("unused.exe"),
            targets=("setup", "portable"),
            dry_run=True,
        )
    )
    assert isinstance(plan, dict)
    assert plan["targets"] == ["setup", "portable"]
    assert plan["publish"] is False


def test_gateway_build_dry_run_accepts_new_isolated_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "current-candidate"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_gateway.py",
            "--platform",
            "win",
            "--arch",
            "x64",
            "--targets",
            "portable",
            "--node",
            "unused-in-dry-run.exe",
            "--output-dir",
            str(output),
            "--dry-run",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    plan = json.loads(result.stdout)
    assert Path(plan["output"]) == output.resolve()
    assert plan["targets"] == ["portable"]


def test_installer_preserves_programdata_and_uses_dedicated_service_identity() -> None:
    installer = (ROOT / "apps/gateway/build/installer.nsi").read_text(encoding="utf-8")
    deployment = json.loads(
        (ROOT / "apps/gateway/build/deployment.json").read_text(encoding="utf-8")
    )
    assert deployment["installer"]["preserve_state_on_uninstall"] is True
    assert deployment["installer"]["compiler"] == {
        "package_version": "3.0.4.1",
        "reported_version": "v3.04",
        "archive": "nsis-3.0.4.1.7z",
        "archive_sha256": "9877df902530f96357d13a7a31ae2b9df67f48b11ffc9a1700a7c961574ec5fa",
        "makensis_sha256": "f2b2b7726ac0d4e720dff52bfca11a5518d550fc75ed34a48dc47921527293f0",
    }
    assert deployment["installer"]["service_account"] == r"NT SERVICE\RACP Gateway"
    assert deployment["installer"]["updater_service_account"] == "LocalSystem"
    assert deployment["installer"]["updater_start"] == "demand"
    assert "Remove-GatewayService.ps1" in installer
    assert 'RMDir /r "$COMMONAPPDATA\\RACP\\Gateway"' not in installer
    assert "$COMMONAPPDATA" not in installer
    assert "SetShellVarContext all" in installer
    assert installer.count("SetRegView 64") == 3
    assert "$APPDATA\\RACP\\Gateway" in installer
    assert '"state_root": ".."' in installer
    assert '"state_root": "$APPDATA\\RACP\\Gateway"' not in installer
    assert "Page instfiles /ENABLECANCEL" in installer
    assert "Var FreshInstall" in installer
    assert "Function CleanupFreshInstall" in installer
    assert "Function .onUserAbort" in installer
    assert "Function .onInstFailed" in installer
    assert 'ReadRegStr $0 HKLM "Software\\RACP\\Gateway" "InstallDir"' in installer
    assert "ProgramData is deliberately preserved even when a fresh install is aborted" in installer
    assert (
        'DeleteRegKey HKLM "SYSTEM\\CurrentControlSet\\Services\\EventLog\\Application\\'
        'RACP Gateway"'
        in installer
    )
    assert 'RMDir /r /REBOOTOK "$INSTDIR"' in installer
    assert "ProgramData is deliberately preserved" in installer

    install = (ROOT / "scripts/host/Install-GatewayService.ps1").read_text(encoding="utf-8")
    remove = (ROOT / "scripts/host/Remove-GatewayService.ps1").read_text(encoding="utf-8")
    assert "RACP Gateway Updater" in install
    gateway_create = (
        "New-Service -Name $serviceName -BinaryPathName $binaryPath "
        "-StartupType Automatic"
    )
    assert gateway_create in install
    assert "sc.exe config $serviceName obj= 'NT SERVICE\\RACP Gateway'" in install
    assert (
        "New-Service -Name $updaterName -BinaryPathName $updaterBinaryPath -StartupType Manual"
        in install
    )
    assert "runtime\\pythonservice.exe" in install
    assert "racp_gateway.windows_service.GatewayService" in install
    assert "racp_gateway.updater_service.GatewayUpdaterService" in install
    assert "ConfigFile" in install
    assert "UTF8Encoding($false)" in install
    assert "WaitForStatus('Running'" in install
    assert "NT AUTHORITY\\SYSTEM:(OI)(CI)F" in install
    assert "(A;;LCRP;;;{0})" in install
    assert "RACP Gateway Updater" in remove
    assert "Services\\EventLog\\Application\\RACP Gateway" in remove


def test_gateway_runtime_declares_update_manifest_version_dependency() -> None:
    project = tomllib.loads((ROOT / "apps/gateway/pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert any(value.startswith("packaging") for value in dependencies)
