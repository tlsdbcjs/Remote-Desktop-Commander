"""Host launcher: real TLS enrollment, self-contained files and safe failures."""

import asyncio
import shutil
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from racp_gateway.app import create_app
from racp_gateway.store import GatewayStore
from racp_sdk.connection_file import ConnectionFile
from racp_sdk.security import SecretStore, digest, tls_context, token
from tls_fixture import certificates

from scripts import create_connection_file as creator


@pytest_asyncio.fixture
async def ticket_lab(tmp_path: Path) -> Any:
    lab = tmp_path / "host lab"
    lab.mkdir()
    ca, cert, key = certificates(tmp_path / "tls")
    shutil.copy2(ca, lab / "ca.pem")
    owner = token()
    store = GatewayStore(lab / "gateway/gateway.db")
    store.initialize(digest(owner))
    store.close()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    gateway = f"https://127.0.0.1:{listener.getsockname()[1]}"
    SecretStore(lab / "owner.bin").save({"token": owner, "gateway": gateway})
    # Deliberately omit client_ca_pem: creator must embed the host public CA itself.
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(lab / "gateway", public_origin=gateway),
            log_level="error",
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            lifespan="on",
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        yield lab, gateway
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


async def test_created_file_is_self_contained_one_use_and_preserves_previous_files(
    ticket_lab: Any,
    tmp_path: Path,
) -> None:
    lab, gateway = ticket_lab
    output = tmp_path / "connection files"
    first, expiry = await asyncio.to_thread(creator.create_file, lab, output, "Remote PC")
    original = first.read_bytes()
    second, _ = await asyncio.to_thread(creator.create_file, lab, output, "Another PC")
    assert first != second and first.read_bytes() == original
    assert sorted(p.suffix for p in output.iterdir()) == [".racp", ".racp"]
    connection = ConnectionFile.model_validate_json(original)
    assert connection.gateway == gateway and not connection.expired()
    assert connection.expires_at == expiry
    assert connection.ca_pem == (lab / "ca.pem").read_text()
    # The transferred document alone supplies TLS trust and its one-use token.
    async with httpx.AsyncClient(
        verify=tls_context(lab / "ca.pem"),
        trust_env=False,
    ) as http:
        registered = await http.post(gateway + "/agent/v1/enroll", json={"token": connection.token})
        assert registered.status_code == 200
        reused = await http.post(gateway + "/agent/v1/enroll", json={"token": connection.token})
        assert reused.status_code == 401


def test_unreachable_gateway_never_prints_secrets_or_creates_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable(*args: Any) -> Any:
        raise httpx.ConnectError("private-owner-secret")

    monkeypatch.setattr(creator, "request_connection", unavailable)
    output = tmp_path / "new output"
    assert (
        creator.main(
            [
                "--lab-dir",
                str(tmp_path),
                "--output-dir",
                str(output),
                "--no-open",
            ]
        )
        == 1
    )
    assert "private-owner-secret" not in capsys.readouterr().out
    assert not output.exists()


def test_auto_detection_refuses_ambiguous_host_owner_states(tmp_path: Path) -> None:
    for name in ["one", "two"]:
        folder = tmp_path / ".racp" / name
        folder.mkdir(parents=True)
        (folder / "owner.bin").touch()
        (folder / "ca.pem").touch()
    with pytest.raises(ValueError, match="single configured"):
        creator.find_lab(tmp_path)
