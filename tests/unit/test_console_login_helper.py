"""Console login helper fails safely and stores only an owner-readable short-lived code."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import create_console_login as helper


def mocked_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.Client
    monkeypatch.setattr(
        helper.SecretStore,
        "load",
        lambda _: {
            "token": "private-host-owner-secret",
            "gateway": "https://gateway.example",
        },
    )
    monkeypatch.setattr(helper, "tls_context", lambda _: True)
    monkeypatch.setattr(
        helper.httpx,
        "Client",
        lambda **args: real(
            transport=httpx.MockTransport(handler),
            **args,
        ),
    )


def test_console_login_code_excludes_owner_and_restricts_file_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "s" * 40

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/":
            return httpx.Response(200, text="Console")
        assert request.url.path == "/api/v1/console/setup-token"
        assert request.headers["authorization"] == "Bearer private-host-owner-secret"
        return httpx.Response(200, json={"setup_secret": secret, "expires_in_seconds": 300})

    mocked_client(monkeypatch, respond)
    file, url = helper.create_login(tmp_path)
    text = file.read_text(encoding="utf-8")
    assert url == "https://gateway.example/console/" and secret in text
    assert "private-host-owner-secret" not in text
    import os

    if os.name == "nt":
        import win32security

        acl = win32security.GetNamedSecurityInfo(
            str(file),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        ).GetSecurityDescriptorDacl()
        assert acl.GetAceCount() == 1  # No inherited Everyone/Users grants.
    else:
        assert file.stat().st_mode & 0o777 == 0o600


def test_unavailable_console_does_not_issue_a_login_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(404)

    mocked_client(monkeypatch, respond)
    with pytest.raises(httpx.HTTPStatusError):
        helper.create_login(tmp_path)
    assert requests == ["GET"] and not (tmp_path / "console-login").exists()
