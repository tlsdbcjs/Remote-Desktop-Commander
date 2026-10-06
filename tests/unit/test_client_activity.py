import json
from pathlib import Path

import pytest
from racp_agent.client_activity import activity


def test_activity_is_bounded_and_only_returns_safe_typed_fields(tmp_path: Path) -> None:
    folder = tmp_path / "background"
    folder.mkdir()
    secret = "private-token-file-content-command-argument"
    records = ["unstructured error " + secret, json.dumps({"event": [secret]})]
    records.extend(
        json.dumps(
            {
                "event": "agent_result",
                "operation": "shell.exec",
                "state": "SUCCEEDED",
                "timestamp": "2026-10-05T08:00:00+00:00",
                "operation_id": "op_" + "a" * 32,
                "payload": secret,
                "stdout": secret,
                "argv": [secret],
            }
        )
        for _ in range(200)
    )
    (folder / "agent.log").write_text("\n".join(records), encoding="utf-8")
    result = activity(tmp_path / "credential.bin")
    assert len(result["events"]) == 40 and result["tail_limited"]
    assert secret not in json.dumps(result)
    assert result["events"][-1]["operation"] == "shell.exec"
    assert result["events"][-1]["state"] == "SUCCEEDED"


def test_activity_rejects_malformed_unknown_fields_and_handles_partial_lines(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "background"
    folder.mkdir()
    raw = [
        {"event": "agent_result", "operation": ["secret"], "state": {"secret": 1}},
        {"event": "unknown_event", "message": "secret"},
        {"event": "agent_connected", "timestamp": "secret", "operation_id": "secret"},
    ]
    (folder / "agent.log").write_text(
        "\n".join(map(json.dumps, raw)) + '\n{"partial":', encoding="utf-8"
    )
    result = activity(tmp_path / "credential.bin")
    assert result["events"] == [{"event": "agent_result"}, {"event": "agent_connected"}]
    assert "secret" not in json.dumps(result)


def test_activity_of_new_pc_is_empty_and_does_not_create_files(tmp_path: Path) -> None:
    assert activity(tmp_path / "credential.bin") == {"events": [], "tail_limited": False}
    assert not (tmp_path / "background").exists()


def test_activity_does_not_follow_log_link(tmp_path: Path) -> None:
    target = tmp_path / "private.txt"
    target.write_text('{"event":"agent_connected"}', encoding="utf-8")
    folder = tmp_path / "background"
    folder.mkdir()
    try:
        (folder / "agent.log").symlink_to(target)
    except OSError:
        pytest.skip("Local OS does not allow symlink creation")
    with pytest.raises(ValueError):
        activity(tmp_path / "credential.bin")
