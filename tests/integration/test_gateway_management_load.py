from pathlib import Path

from scripts.gateway_management_load import run_load


def test_gateway_management_load_fixture_is_bounded_and_cleans_up(tmp_path: Path) -> None:
    evidence = tmp_path / "load-evidence"
    result = run_load(
        evidence,
        devices=50,
        audit_rows=500,
        iterations=2,
        target_seconds=10.0,
    )
    assert result["fixture"]["synthetic_devices"] == 50
    assert result["fixture"]["audit_rows"] == 500
    assert result["device_metadata"]["target_met"] is True
    assert result["audit_search"]["target_met"] is True
    assert result["sqlite_writer_contention"]["writer_was_blocked"] is True
    assert result["sqlite_writer_contention"]["write_after_release"] == "pass"
    assert result["cleanup"] == {"fixture_owned": True, "fixture_removed": True}
    assert not (evidence / "fixture").exists()
    assert (evidence / "result.json").is_file()
