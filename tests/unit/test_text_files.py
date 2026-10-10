import codecs
import hashlib
import threading
import time

import pytest
from racp_agent.providers.filesystem import Budget, FilesystemProvider
from racp_agent.providers.text_files import patch_batch, search_content
from racp_domain.models import RACPError
from racp_protocol.registry import validate_payload


def budget(gate=None):
    return Budget(time.monotonic() + 10, threading.Event(), gate)


def target(path, raw, old="old", new="new"):
    return {
        "path": path,
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "edits": [{"old_text": old, "new_text": new}],
    }


def test_literal_search_bounds_binary_and_casefold_coordinates(tmp_path):
    provider = FilesystemProvider(tmp_path, tmp_path / "spool")
    (tmp_path / "one.txt").write_text("Needle\nStraße\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"Needle\x00hidden")
    (tmp_path / "large.txt").write_bytes(b"x" * 100)
    payload = validate_payload(
        "filesystem.search_content",
        {
            "path": ".",
            "pattern": "STRASSE",
            "case_sensitive": False,
            "max_file_bytes": 32,
        },
    )
    value = search_content(provider, tmp_path, payload, budget())
    assert len(value["items"]) == 1
    assert value["items"][0]["line"] == 2 and value["items"][0]["column"] == 1
    assert value["skipped"]["binary"] == 1 and value["skipped"]["oversized"] == 1
    (tmp_path / "many.txt").write_text("hit\nhit\nhit\n")
    bounded = search_content(
        provider,
        tmp_path / "many.txt",
        validate_payload(
            "filesystem.search_content", {"path": "many.txt", "pattern": "hit", "max_results": 1}
        ),
        budget(),
    )
    assert len(bounded["items"]) == 1 and "result_budget" in bounded["limits_reached"]
    exhausted = search_content(
        provider,
        tmp_path / "many.txt",
        validate_payload(
            "filesystem.search_content", {"path": "many.txt", "pattern": "hit", "max_scan_bytes": 1}
        ),
        budget(),
    )
    assert not exhausted["items"] and exhausted["scan_bytes_reserved"] == 0
    assert exhausted["limits_reached"] == ["scan_byte_budget"]
    (tmp_path / "long.txt").write_text("ß" * 500 + "Needle", encoding="utf-8")
    late = search_content(
        provider,
        tmp_path / "long.txt",
        validate_payload(
            "filesystem.search_content",
            {"path": "long.txt", "pattern": "needle", "case_sensitive": False},
        ),
        budget(),
    )
    assert late["items"][0]["column"] == 501
    assert "Needle" in late["items"][0]["preview"]


def test_search_never_traverses_links(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("PRIVATE_OUTSIDE")
    (workspace / "link").symlink_to(outside)
    provider = FilesystemProvider(workspace, tmp_path / "spool")
    result = search_content(
        provider,
        workspace,
        validate_payload("filesystem.search_content", {"path": ".", "pattern": "PRIVATE_OUTSIDE"}),
        budget(),
    )
    assert result["items"] == [] and result["skipped"] == {"link_or_reparse": 1}


def test_batch_preflight_dry_run_and_unicode_bom_preserve_bytes(tmp_path):
    provider = FilesystemProvider(tmp_path, tmp_path / "spool")
    original = codecs.BOM_UTF8 + "old\r\n다음\n".encode()
    (tmp_path / "a.txt").write_bytes(original)
    (tmp_path / "b.txt").write_bytes(b"different")
    bad = validate_payload(
        "filesystem.patch", {"files": [target("a.txt", original), target("b.txt", b"different")]}
    )
    with pytest.raises(RACPError, match="occurrence"):
        patch_batch(provider, bad, budget())
    assert (tmp_path / "a.txt").read_bytes() == original
    good = validate_payload(
        "filesystem.patch", {"files": [target("a.txt", original)], "dry_run": True}
    )
    preview = patch_batch(provider, good, budget())
    assert preview["dry_run"] and not preview["applied"] and "-old" in preview["files"][0]["diff"]
    assert (tmp_path / "a.txt").read_bytes() == original
    good["dry_run"] = False
    result = patch_batch(provider, good, budget())
    changed = codecs.BOM_UTF8 + "new\r\n다음\n".encode()
    assert (tmp_path / "a.txt").read_bytes() == changed
    assert result["files"][0]["sha256"] == hashlib.sha256(changed).hexdigest()
    assert result["atomic_per_file"] and not result["atomic_batch"]
    assert not list(tmp_path.glob(".racp-*.tmp"))


def test_late_batch_failure_reports_only_committed_files(tmp_path, monkeypatch):
    provider = FilesystemProvider(tmp_path, tmp_path / "spool")
    for name in ["a.txt", "b.txt"]:
        (tmp_path / name).write_bytes(b"old")
    original = provider.write

    def fail_second(path, payload, execution_budget):
        if path.name == "b.txt":
            raise PermissionError("owned failure")
        return original(path, payload, execution_budget)

    monkeypatch.setattr(provider, "write", fail_second)
    with pytest.raises(RACPError) as raised:
        patch_batch(
            provider,
            validate_payload(
                "filesystem.patch",
                {
                    "files": [target("a.txt", b"old"), target("b.txt", b"old")],
                },
            ),
            budget(),
        )
    assert (tmp_path / "a.txt").read_bytes() == b"new"
    assert (tmp_path / "b.txt").read_bytes() == b"old"
    assert raised.value.error.execution_state == "completed"
    assert raised.value.error.details["completed"] == [
        {"path": str(tmp_path / "a.txt"), "sha256": hashlib.sha256(b"new").hexdigest()}
    ]


def test_worker_permission_gate_rechecks_before_publication(tmp_path, monkeypatch):
    provider = FilesystemProvider(tmp_path, tmp_path / "spool")
    (tmp_path / "a.txt").write_bytes(b"old")
    calls = 0
    revoked = False
    original = provider.precondition

    def precondition(*args):
        nonlocal calls, revoked
        original(*args)
        calls += 1
        if calls == 2:
            revoked = True

    def gate():
        if revoked:
            raise RACPError("PERMISSION_DENIED", "owned revocation")

    monkeypatch.setattr(provider, "precondition", precondition)
    with pytest.raises(RACPError, match="revocation"):
        patch_batch(
            provider,
            validate_payload("filesystem.patch", {"files": [target("a.txt", b"old")]}),
            budget(gate),
        )
    assert (tmp_path / "a.txt").read_bytes() == b"old" and not list(tmp_path.glob(".racp-*.tmp"))


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("filesystem.search_content", {"path": ".", "pattern": "x\ny"}),
        ("filesystem.search_content", {"path": ".", "pattern": "x", "max_files": True}),
        ("filesystem.patch", {"files": []}),
        ("filesystem.patch", {"files": [{"path": "a.txt", "edits": []}]}),
    ],
)
def test_text_operations_reject_unbounded_or_ambiguous_inputs(operation, payload):
    with pytest.raises(RACPError):
        validate_payload(operation, payload)
