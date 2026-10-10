"""Gateway deployment: protected state, restart identity and occupied-port refusal."""

import json
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography import x509
from racp_sdk.security import SecretStore

from scripts import gateway_host as host


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_setup_protects_state_and_refuses_reinitialization(tmp_path: Path) -> None:
    state = tmp_path / "host state"
    host.setup(state, "127.0.0.1", free_port())
    credential = SecretStore(state / "owner.bin").load()
    assert credential["gateway"].startswith("https://127.0.0.1:")
    assert (state / "gateway/gateway.db").is_file()
    cert = x509.load_pem_x509_certificate((state / "server.pem").read_bytes())
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days == 365
    assert "server.key" in {p.name for p in state.iterdir()}
    assert credential["token"] not in (state / "host.json").read_text()
    if host.os.name == "nt":
        import win32security

        security = win32security.GetNamedSecurityInfo(
            str(state), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        import win32api
        import win32con

        process_token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        try:
            sid = win32security.GetTokenInformation(process_token, win32security.TokenUser)[0]
        finally:
            process_token.Close()
        acl = security.GetSecurityDescriptorDacl()
        assert acl.GetAceCount() > 0
        assert all(acl.GetAce(i)[2] == sid for i in range(acl.GetAceCount()))
    before = (state / "owner.bin").read_bytes()
    with pytest.raises(FileExistsError):
        host.setup(state, "127.0.0.1", free_port())
    assert (state / "owner.bin").read_bytes() == before


def test_occupied_port_never_creates_state_or_terminates_other_listener(tmp_path: Path) -> None:
    state = tmp_path / "new host"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(OSError):
            host.setup(state, "127.0.0.1", listener.getsockname()[1])
        assert not state.exists()
        assert listener.getsockname()[0] == "127.0.0.1"


def test_restart_preserves_identity_and_persists_valid_oauth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "host"
    port = free_port()
    host.setup(state, "127.0.0.1", port)
    before = (state / "owner.bin").read_bytes()
    oauth = tmp_path / "oauth.json"
    oauth.write_text(
        json.dumps(
            {
                "version": 1,
                "issuer": "https://identity.example/realm",
                "jwks_uri": "https://identity.example/keys",
                "owner_subject": "owner",
                "client_ids": ["codex-client"],
            }
        )
    )
    spawned = Mock(return_value=Mock(wait=Mock(return_value=0)))
    monkeypatch.setattr(host.subprocess, "Popen", spawned)
    assert host.start(state, None, None, oauth) == 0
    assert "--oauth-config" in spawned.call_args.args[0]
    assert host.start(state, None, None, None) == 0
    assert spawned.call_args.args[0][-1] == str(oauth)
    assert (state / "owner.bin").read_bytes() == before
    with pytest.raises(ValueError, match="cannot be overwritten"):
        host.start(state, "127.0.0.2", None, None)
