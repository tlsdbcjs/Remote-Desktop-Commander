"""Unit tests for RACP version definitions and version management logic."""

from pathlib import Path

import pytest
from racp_domain.version import VERSION, __version__

from scripts.version import calculate_next_version, parse_semver


def test_package_version_constants() -> None:
    assert VERSION == "0.1.11"
    assert __version__ == VERSION

    import racp_agent
    import racp_cli
    import racp_gateway
    import racp_observability
    import racp_policy
    import racp_protocol
    import racp_sdk

    assert racp_agent.__version__ == VERSION
    assert racp_cli.__version__ == VERSION
    assert racp_gateway.__version__ == VERSION
    assert racp_protocol.__version__ == VERSION
    assert racp_policy.__version__ == VERSION
    assert racp_observability.__version__ == VERSION
    assert racp_sdk.__version__ == VERSION


def test_parse_semver() -> None:
    assert parse_semver("0.1.8") == (0, 1, 8)
    assert parse_semver("1.0.0") == (1, 0, 0)
    assert parse_semver("10.20.30") == (10, 20, 30)

    with pytest.raises(ValueError, match="Invalid semantic version"):
        parse_semver("0.1")

    with pytest.raises(ValueError, match="Invalid semantic version"):
        parse_semver("v0.1.8")

    with pytest.raises(ValueError, match="Invalid semantic version"):
        parse_semver("0.1.8-alpha")


def test_calculate_next_version_patch_autoincrement() -> None:
    # Default behavior: auto-increment patch
    assert calculate_next_version("0.1.8") == "0.1.9"
    assert calculate_next_version("0.1.9") == "0.1.10"
    assert calculate_next_version("1.0.0") == "1.0.1"


def test_calculate_next_version_minor() -> None:
    # Minor increment resets patch to 0
    assert calculate_next_version("0.1.8", bump_minor=True) == "0.2.0"
    # Specific minor value resets patch to 0
    assert calculate_next_version("0.1.8", minor_val=5) == "0.5.0"


def test_calculate_next_version_major() -> None:
    # Major increment resets minor and patch to 0
    assert calculate_next_version("0.1.8", bump_major=True) == "1.0.0"
    # Specific major value resets minor and patch to 0
    assert calculate_next_version("0.1.8", major_val=2) == "2.0.0"


def test_calculate_next_version_combinations() -> None:
    assert calculate_next_version("0.1.8", major_val=2, minor_val=3, patch_val=4) == "2.3.4"
    assert calculate_next_version("0.1.8", patch_val=12) == "0.1.12"


def test_version_updates_rust_and_tauri(tmp_path: Path) -> None:
    from scripts.version import update_file

    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[workspace.package]\nversion = "0.1.10"\n')
    assert update_file(cargo, "0.1.10", "0.1.11", False)
    assert 'version = "0.1.11"' in cargo.read_text()
    tauri = tmp_path / "tauri.conf.json"
    tauri.write_text('{"version":"0.1.10"}')
    assert update_file(tauri, "0.1.10", "0.1.11", False)
    assert '"version":"0.1.11"' in tauri.read_text()
