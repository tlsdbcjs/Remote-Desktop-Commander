import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from racp_agent.broker.identity import Identity
from racp_agent.service_config import ServiceConfig
from racp_agent.service_host import ServiceRunner


def config(root: Path) -> ServiceConfig:
    return ServiceConfig(
        service_sid="S-1-5-80-1-2-3-4-5",
        agent_sid="S-1-5-21-1",
        credentials=root / "credential.bin",
        data_dir=root / "data",
        workspace=root / "workspace",
    )


def test_service_config_rejects_unexpected_identity_admin_system_and_relative_paths(
    tmp_path: Path,
) -> None:
    value = config(tmp_path)
    actor = Identity(1, 1.0, value.agent_sid, 0, 8192, (value.service_sid,))
    value.check_identity(actor)
    for bad in [
        replace(actor, service_sids=()),
        replace(actor, administrator=True),
        replace(actor, session=1),
        replace(actor, sid="S-1-5-18"),
    ]:
        with pytest.raises(PermissionError):
            value.check_identity(bad)
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate({**value.model_dump(), "credentials": Path("relative.bin")})
    assert value.profile == "read_only"


async def test_service_stop_cross_thread_cancels_agent_and_waits_for_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import racp_agent.service_host as host

    value = config(tmp_path)
    actor = Identity(1, 1.0, value.agent_sid, 0, 8192, (value.service_sid,))
    started = asyncio.Event()

    class Journal:
        closed = False

        def close(self) -> None:
            self.closed = True

    class FakeAgent:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.stopping = asyncio.Event()
            self.journal = Journal()
            self.cleaned = False

        async def run(self) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cleaned = True

    class Credentials:
        def __init__(self, path: Path) -> None:
            pass

        def load(self) -> dict[str, str]:
            return {"device_id": "dev_test", "gateway": "http://127.0.0.1:1", "credential": "test"}

    monkeypatch.setattr(host, "process_identity", lambda pid: actor)
    monkeypatch.setattr(host, "SecretStore", Credentials)
    monkeypatch.setattr(host, "Agent", FakeAgent)
    runner = ServiceRunner(value)
    task = asyncio.create_task(runner.run())
    await started.wait()
    await asyncio.to_thread(runner.stop)
    await asyncio.wait_for(task, 2)
    assert runner.agent is not None
    assert runner.agent.stopping.is_set() and runner.agent.journal.closed
    assert runner.agent.cleaned
