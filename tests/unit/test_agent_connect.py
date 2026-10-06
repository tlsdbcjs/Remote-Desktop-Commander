import concurrent.futures
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from racp_agent.connect import enroll
from racp_agent.settings import saved_settings
from racp_domain.models import RACPError
from racp_sdk.security import SecretStore, token


def provider(monkeypatch: pytest.MonkeyPatch, result: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://gateway.example/agent/v1/enroll"
        assert "Authorization" not in request.headers
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=result)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    return calls


def test_enrollment_preserves_identity_profile_paths_and_tls_without_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret, credential = token(), token()
    calls = provider(monkeypatch, {"device_id": "dev_new_pc", "credential": credential})
    workspace = tmp_path / "자료 폴더"
    workspace.mkdir()
    path, settings = enroll("https://gateway.example/", workspace, tmp_path / "state", secret)
    stored = SecretStore(path).load()
    assert calls == [{"token": secret}]
    assert saved_settings(stored) == settings
    assert settings.profile == "read_only" and settings.gateway == "https://gateway.example"
    assert settings.workspace == workspace and settings.data_dir.is_absolute()
    assert secret not in path.read_text(errors="replace")
    if os.name == "nt":
        assert credential.encode() not in path.read_bytes()
    else:
        assert not path.stat().st_mode & 0o077
    with pytest.raises(ValueError, match="identity differ"):
        saved_settings({**stored, "device_id": "dev_other"})
    with pytest.raises(RACPError, match="already configured"):
        enroll("https://gateway.example", workspace, path.parent, token())
    assert len(calls) == 1 and SecretStore(path).load() == stored


@pytest.mark.parametrize("invalid", ["workspace", "existing-state", "plain-url", "in-progress"])
def test_local_setup_failure_does_not_consume_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    calls = provider(monkeypatch, {"device_id": "dev_unused", "credential": token()})
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    gateway = "https://gateway.example"
    if invalid == "workspace":
        workspace = tmp_path / "missing"
    elif invalid == "existing-state":
        SecretStore(state / "credential.bin").save({"credential": "keep-original"})
    elif invalid == "plain-url":
        gateway = "http://gateway.example"
    else:
        (state / ".enrollment-in-progress").write_text("owned by another setup")
    with pytest.raises((RACPError, ValueError, OSError)):
        enroll(gateway, workspace, state, token())
    assert not calls
    if invalid == "existing-state":
        assert SecretStore(state / "credential.bin").load() == {"credential": "keep-original"}
    if invalid == "in-progress":
        assert (state / ".enrollment-in-progress").read_text() == "owned by another setup"


def test_enrolled_but_unsaved_credential_reports_unknown_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = token()
    calls = provider(monkeypatch, {"device_id": "dev_lost", "credential": credential})
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def exhausted(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected disk failure")

    monkeypatch.setattr(SecretStore, "save", exhausted)
    with pytest.raises(RACPError) as result:
        enroll("https://gateway.example", workspace, tmp_path / "state", token())
    assert result.value.error.code == "EXECUTION_UNKNOWN"
    assert result.value.error.execution_state == "unknown"
    assert result.value.error.details["device_id"] == "dev_lost"
    assert credential not in str(result.value)
    assert len(calls) == 1


def test_concurrent_new_credential_publication_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "credentials.bin"

    def publish(value: str) -> bool:
        try:
            SecretStore(path).save({"credential": value}, overwrite=False)
            return True
        except FileExistsError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(publish, ["first", "second"]))
    assert sum(outcomes) == 1
    assert SecretStore(path).load()["credential"] == ("first" if outcomes[0] else "second")
    assert list(tmp_path.iterdir()) == [path]


def test_oversized_or_nonstring_credentials_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "credentials.bin"
    invalid: Any = {"credential": 123}
    with pytest.raises(ValueError):
        SecretStore(path).save(invalid)
    with pytest.raises(ValueError):
        SecretStore(path).save({"credential": "x" * 32768})
    path.write_bytes(b"x" * 65537)
    path.chmod(0o600)
    with pytest.raises(ValueError, match="64 KiB"):
        SecretStore(path).load()
    if os.name == "nt":
        path.write_bytes(b"invalid-protected-credential")
        with pytest.raises(PermissionError, match="OS identity"):
            SecretStore(path).load()


def test_linked_state_does_not_enroll_or_touch_other_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    workspace, other, state = tmp_path / "workspace", tmp_path / "other", tmp_path / "linked"
    workspace.mkdir()
    other.mkdir()
    if os.name == "nt":
        result = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(state), str(other)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
    else:
        state.symlink_to(other, target_is_directory=True)
    calls = provider(monkeypatch, {"device_id": "dev_unused", "credential": token()})
    try:
        with pytest.raises(PermissionError):
            enroll("https://gateway.example", workspace, state, token())
        assert not calls and not list(other.iterdir())
    finally:
        if os.name == "nt":
            state.rmdir()
        else:
            state.unlink()
