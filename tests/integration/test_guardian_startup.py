import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from racp_agent.broker import guard_task
from racp_agent.broker.guard_client import pin_guard
from racp_agent.broker.guard_config import Controller, GuardConfig
from racp_agent.broker.identity import process_identity
from racp_agent.broker.supervisor import BrokerSupervisor

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler startup lifetime")


@pytest.mark.parametrize("failure", ["timeout", "unhealthy"])
async def test_unready_guardian_task_process_stops_before_private_config_is_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    actor = process_identity(os.getpid())
    if actor.session == 0:
        pytest.skip("requires a logged-on user session")
    broker = BrokerSupervisor(tmp_path / "brokers", actor.session, "dev_guard_startup")
    pinned = []
    launched: list[Path] = []
    original = guard_task.guard_request
    launch = guard_task.launch_task

    def observe_launch(config: GuardConfig, file: Path) -> str:
        name = launch(config, file)
        launched.append(file)
        return name

    def unavailable(config: GuardConfig, operation: str) -> dict[str, Any]:
        state = original(config, operation)
        if not pinned:
            ready = config.model_copy(
                update={
                    "guardian_pid": state["guardian_pid"],
                    "guardian_created": state["guardian_created"],
                }
            )
            pinned.append(pin_guard(ready))
        if failure == "timeout":
            raise TimeoutError("injected lost readiness receipt")
        return {**state, "healthy": False}

    try:
        await broker.start()
        assert broker.config is not None
        parent = process_identity(broker.status["broker_pid"])
        config = GuardConfig.pair_guard(
            parent,
            actor.sid,
            Controller(pid=actor.pid, created=actor.created, sid=actor.sid, session=actor.session),
            broker.config.job_name,
        )
        monkeypatch.setattr(guard_task, "launch_task", observe_launch)
        monkeypatch.setattr(guard_task, "guard_request", unavailable)
        with pytest.raises(TimeoutError if failure == "timeout" else PermissionError):
            await asyncio.to_thread(guard_task.start_scheduled, config)
        assert len(pinned) == len(launched) == 1
        # Native process signal, not just absence of its task definition.
        assert await asyncio.wait_for(pinned[0].wait(), 5) is not None
        assert not launched[0].parent.exists()
        assert broker.process is not None and broker.process.returncode is None
    finally:
        for peer in pinned:
            peer.close()
        await broker.finish_stop()


def test_startup_stop_failure_keeps_private_task_recovery_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = process_identity(os.getpid())
    if actor.session == 0:
        pytest.skip("requires a logged-on user session")
    directory = tmp_path / "racp-input-guard-retained"
    directory.mkdir()
    config = GuardConfig.pair_guard(actor, actor.sid)
    monkeypatch.setattr(guard_task.tempfile, "mkdtemp", lambda **_: str(directory))
    monkeypatch.setattr(
        guard_task, "launch_task", lambda config, file: "RACP-InputGuard-" + config.pair_id
    )

    def failure(*_: Any) -> Any:
        raise PermissionError("injected readiness/stop refusal")

    monkeypatch.setattr(guard_task, "guard_request", failure)
    monkeypatch.setattr(guard_task, "stop_startup_task", failure)
    removed = []
    monkeypatch.setattr(guard_task, "remove_task", lambda name: removed.append(name))
    with pytest.raises(guard_task.StartupCleanupUnverified) as error:
        guard_task.start_scheduled(config)
    assert error.value.error.execution_state == "unknown"
    assert error.value.error.details["cleanup_status"] == "unverified"
    assert GuardConfig.load(directory / (config.pair_id + ".json")).pair_id == config.pair_id
    assert not removed
