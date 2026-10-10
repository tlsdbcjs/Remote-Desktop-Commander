import io
import json
import ssl
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from racp_agent import desktop_control
from racp_domain.models import RACPError
from racp_sdk.security import SecretStore


def test_desktop_bridge_rejects_execution_and_extra_arguments() -> None:
    for value in [
        {"action": "exec", "argv": ["anything"]},
        {"action": "status", "token": "private-enrollment-secret"},
    ]:
        with pytest.raises(ValidationError):
            desktop_control.REQUEST.validate_python(value)


def test_desktop_info_of_unconfigured_pc_does_not_create_state(tmp_path: Path) -> None:
    credentials = tmp_path / "absent" / "credential.bin"
    result = desktop_control.information(credentials)
    assert result["configured"] is False
    assert result["execution_identity"]
    assert not credentials.parent.exists()


@pytest.mark.parametrize("desktop_enabled", [False, True])
async def test_manual_enrollment_persists_desktop_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop_enabled: bool
) -> None:
    original = httpx.Client
    issued_credential = "issued-device-credential-fixture"

    def reply(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"token": "private-enrollment-secret"}
        return httpx.Response(
            200, json={"device_id": "dev_manual_fixture", "credential": issued_credential}
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs)
    )
    state = tmp_path / "state"
    request = desktop_control.Enrollment(
        action="enroll",
        gateway="https://gateway.example",
        workspace=tmp_path,
        token="private-enrollment-secret",
        desktop_enabled=desktop_enabled,
    )
    result = await desktop_control.execute(request, state)
    assert result["configured"] and result["desktop_enabled"] is desktop_enabled
    saved = SecretStore(state / "credential.bin").load()
    assert saved["credential"] == issued_credential
    assert request.token not in json.dumps(saved)
    assert (
        desktop_control.information(state / "credential.bin")["desktop_enabled"] is desktop_enabled
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"action":"exec","token":"private-enrollment-secret"}\n',
        b'{"action":"info","token":"private-enrollment-secret"}\n',
        b"private-enrollment-secret" * 800,
    ],
)
def test_invalid_private_ipc_never_echoes_secrets(
    raw: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    outgoing = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(desktop_control.sys, "stdin", incoming)
    monkeypatch.setattr(desktop_control.sys, "stdout", outgoing)
    monkeypatch.setattr(desktop_control.sys, "argv", ["bridge", "--state-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        desktop_control.main()
    assert error.value.code == 4
    output = outgoing.buffer.getvalue()
    assert json.loads(output)["ok"] is False
    assert b"private-enrollment-secret" not in output
    assert not (tmp_path / "credential.bin").exists()


@pytest.mark.parametrize(
    "invalid,code",
    [
        ("gateway", "GATEWAY_INVALID"),
        ("workspace", "WORKSPACE_INVALID"),
        ("ca", "CA_INVALID"),
    ],
)
async def test_local_enrollment_preflight_fails_before_consuming_token(
    invalid: str,
    code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Enrollment must not consume a token after local preflight failure")

    monkeypatch.setattr(desktop_control, "enroll", unexpected)
    request = desktop_control.Enrollment(
        action="enroll",
        gateway="https://gateway.example",
        workspace=tmp_path,
        token="private-enrollment-secret",
        ca_file=tmp_path / "missing.pem" if invalid == "ca" else None,
    )
    if invalid == "gateway":
        request.gateway = "http://remote.example"
    elif invalid == "workspace":
        request.workspace = tmp_path / "missing"
    with pytest.raises(RACPError) as raised:
        await desktop_control.execute(request, tmp_path / "state")
    assert desktop_control.failure_code(raised.value, request) == code
    assert not (tmp_path / "state").exists()


def test_safe_diagnostics_distinguish_token_tls_network_and_saved_state() -> None:
    command = desktop_control.Command(action="status")
    rejected = RACPError("UNAUTHENTICATED", "private-enrollment-secret")
    assert desktop_control.failure_code(rejected, command) == "TOKEN_REJECTED"
    assert (
        desktop_control.failure_code(httpx.ConnectError("private-address"), command)
        == "GATEWAY_UNREACHABLE"
    )
    assert (
        desktop_control.failure_code(httpx.ReadTimeout("private-address"), command)
        == "GATEWAY_TIMEOUT"
    )
    failure = httpx.ConnectError("private-certificate-details")
    failure.__cause__ = ssl.SSLCertVerificationError("private-certificate-details")
    assert desktop_control.failure_code(failure, command) == "TLS_FAILED"
    assert (
        desktop_control.failure_code(
            PermissionError("private-path"), desktop_control.Command(action="info")
        )
        == "CONFIG_UNREADABLE"
    )


def test_unreadable_saved_registration_is_preserved_and_diagnosed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "credential.bin"
    original = b"invalid-protected-private-credential"
    path.write_bytes(original)
    path.chmod(0o600)
    incoming = io.TextIOWrapper(io.BytesIO(b'{"action":"info"}\n'), encoding="utf-8")
    outgoing = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(desktop_control.sys, "stdin", incoming)
    monkeypatch.setattr(desktop_control.sys, "stdout", outgoing)
    monkeypatch.setattr(desktop_control.sys, "argv", ["bridge", "--state-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        desktop_control.main()
    reply = json.loads(outgoing.buffer.getvalue())
    assert reply == {"ok": False, "code": "CONFIG_UNREADABLE"}
    assert path.read_bytes() == original
    assert original not in outgoing.buffer.getvalue()
