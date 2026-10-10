"""Windows Hello must use the native account, including a sanitized subprocess environment."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from racp_agent import runtime

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native account lookup")


@pytest.mark.parametrize("spoof", [False, True])
async def test_hello_username_is_native_with_missing_or_spoofed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spoof: bool
) -> None:
    import win32api

    expected = win32api.GetUserName()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent = runtime.Agent(
        "http://127.0.0.1:1", "owned-fixture-credential", "dev_user", workspace, tmp_path / "agent"
    )
    observed = []

    class CapturedHello(Exception):
        pass

    class Connection:
        async def __aenter__(self):
            async def send(message):
                pass

            return SimpleNamespace(send=send)

        async def __aexit__(self, *args):
            pass

    async def capture(message):
        observed.append(message.execution_identity)
        raise CapturedHello

    monkeypatch.setattr(runtime, "connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(agent, "send", capture)
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        if spoof:
            monkeypatch.setenv(name, "RACP_FAKE_ENV_USER")
        else:
            monkeypatch.delenv(name, raising=False)
    try:
        with pytest.raises(CapturedHello):
            await agent.session()
        assert observed == [expected]
    finally:
        agent.journal.db.close()
