import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from racp_domain.models import RACPError
from racp_policy.engine import Decision, Rule, evaluate
from racp_protocol.models import ShellInput, decode_message, new_id
from racp_sdk.journal import Journal
from racp_sdk.security import SecretStore, digest, require_secure_url


def test_shell_requires_explicit_mode_and_rejects_unknown_fields() -> None:
    for payload in (
        {"argv": []},
        {"argv": ["python"], "command": "echo hi"},
        {"mode": "shell", "command": "echo hi"},
        {"argv": ["python"], "typo": True},
        {"argv": ["a\0b"]},
    ):
        with pytest.raises(ValidationError):
            ShellInput.model_validate(payload)


def test_frame_limits_and_discriminator() -> None:
    for raw in (
        '{"type":"REQUEST"}',
        '"' + "a" * (1024 * 1024) + '"',
        "[" * 33 + "0" + "]" * 33,
        json.dumps({"type": "hello", "x": "a" * 262145}),
    ):
        with pytest.raises((ValueError, ValidationError)):
            decode_message(raw)


def test_policy_is_default_deny_and_deny_wins() -> None:
    allow = Rule(frozenset({"shell.exec"}), Decision.ALLOW)
    approval = Rule(frozenset({"shell.exec"}), Decision.REQUIRE_APPROVAL)
    deny = Rule(frozenset({"shell.exec"}), Decision.DENY)
    assert evaluate("shell.exec", ()) == Decision.DENY
    assert evaluate("shell.exec", (allow, approval)) == Decision.REQUIRE_APPROVAL
    assert evaluate("shell.exec", (allow, approval, deny)) == Decision.DENY


def test_journal_dedupe_survives_restart_and_unknown_never_reexecutes(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    request = {"operation_id": new_id("op"), "device_id": "dev_test", "operation": "shell.exec"}
    journal = Journal(path)
    record, fresh = journal.accept("scope", digest("key"), "payload", request)
    assert fresh
    journal.transition(record["id"], "RUNNING")
    journal.close()
    journal = Journal(path)
    journal.recover_agent()
    duplicate, fresh = journal.accept("scope", digest("key"), "payload", request)
    assert not fresh and duplicate["state"] == "UNKNOWN"
    with pytest.raises(RACPError, match="another payload"):
        journal.accept("scope", digest("key"), "changed", request)
    assert journal.transition(record["id"], "SUCCEEDED")["state"] == "UNKNOWN"
    journal.close()


def test_private_credential_storage(tmp_path: Path) -> None:
    secret = "credential-must-not-be-plaintext"
    store = SecretStore(tmp_path / "credential.bin")
    store.save({"token": secret})
    assert store.load() == {"token": secret}
    import os

    if os.name == "nt":
        assert secret.encode() not in store.path.read_bytes()
    else:
        assert not store.path.stat().st_mode & 0o077


def test_remote_endpoint_needs_tls_and_forbids_token_in_url() -> None:
    for url in ("http://192.168.1.1", "https://host?token=secret", "https://user:password@host"):
        with pytest.raises(ValueError):
            require_secure_url(url)
    require_secure_url("http://127.0.0.1:8765")
    require_secure_url("https://gateway.example")
