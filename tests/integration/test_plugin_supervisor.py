import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import psutil
import pytest
from racp_agent.plugins.manifest import load_approved
from racp_agent.plugins.supervisor import PluginSupervisor
from racp_domain.models import RACPError
from racp_protocol.plugins import PluginManifest


def approved_plugin(tmp_path: Path, health: str = "okay") -> PluginSupervisor:
    file = tmp_path / "manifest.json"
    script = Path(__file__).parents[1] / "fixtures/plugin_worker.py"
    manifest = PluginManifest.model_validate(
        {
            "name": "owned-fixture",
            "version": "1.0.0",
            "backend_name": "owned-python-fixture",
            "backend_version": "1",
            "capabilities": ["static-analysis"],
            "command": [
                str(Path(sys.executable).resolve()),
                "-I",
                str(script),
                "--root",
                str(tmp_path),
                "--manifest",
                str(file),
                "--health",
                health,
            ],
            "working_directory": str(tmp_path),
            "required_permissions": ["re.test.execute"],
            "health_timeout_ms": 2000,
            "operations": [
                {
                    "name": "re.command",
                    "capability": "static-analysis",
                    "permission_scope": "re.test.execute",
                    "side_effect": True,
                    "execution_modes": ["sync", "job"],
                    "input_schema": {
                        "type": "object",
                        "required": ["action"],
                        "additionalProperties": False,
                        "properties": {"action": {"type": "string"}, "value": {"type": "integer"}},
                    },
                    "output_schema": {
                        "type": "object",
                        "required": ["value"],
                        "additionalProperties": False,
                        "properties": {"value": {"type": "integer"}},
                    },
                }
            ],
        }
    )
    file.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    return PluginSupervisor(load_approved(file, digest, frozenset({"re.test.execute"})))


async def test_plugin_manifest_handshake_schema_validation_and_private_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "owned-test-secret")
    monkeypatch.setenv("RACP_OWNER_TOKEN", "owned-test-secret")
    plugin = approved_plugin(tmp_path)
    states = []
    plugin.on_state = states.append
    try:
        with pytest.raises(RACPError) as invalid:
            await plugin.request("re.command", {"action": "echo", "unexpected": True}, 5000)
        assert invalid.value.error.code == "INVALID_ARGUMENT" and plugin.owned is None
        assert await plugin.request("re.command", {"action": "echo", "value": 42}, 5000) == {
            "value": 42
        }
        assert await plugin.request("re.command", {"action": "env"}, 5000) == {"value": 0}
        with pytest.raises(RACPError) as rejected:
            await plugin.request("re.command", {"action": "reject"}, 5000)
        assert rejected.value.error.code == "INVALID_ARGUMENT" and plugin.state == "READY"
        await plugin.request("re.command", {"action": "stderr"}, 5000)
        await asyncio.sleep(0.05)
        assert plugin.stderr_bytes == 90000 and len(plugin.stderr_tail) == 65536
        assert any(s["state"] == "READY" for s in states)
    finally:
        await plugin.close()
    assert plugin.last_cleanup == "complete" and plugin.owned is None


@pytest.mark.parametrize("action", ["invalid", "wrong_id", "wrong_instance", "oversized", "flood"])
async def test_plugin_protocol_fault_is_contained_and_expires_the_generation(
    tmp_path: Path, action: str
) -> None:
    plugin = approved_plugin(tmp_path)
    try:
        await plugin.start()
        instance = plugin.instance_id
        with pytest.raises(RACPError) as failure:
            await plugin.request("re.command", {"action": action}, 5000)
        assert failure.value.error.code in {"PLUGIN_PROTOCOL_ERROR", "RESOURCE_EXHAUSTED"}
        assert plugin.state == "DEGRADED" and plugin.owned is None
        assert plugin.last_cleanup == "complete" and plugin.events.qsize() <= 64
        with pytest.raises(RACPError) as expired:
            await plugin.request(
                "re.command", {"action": "echo"}, 5000, expected_instance_id=instance
            )
        assert expired.value.error.code == "HANDLE_EXPIRED"
        assert await plugin.request("re.command", {"action": "echo"}, 5000) == {"value": 0}
        assert plugin.instance_id != instance
    finally:
        await plugin.close()


async def test_plugin_crash_does_not_replay_side_effect_and_restart_budget_is_bounded(
    tmp_path: Path,
) -> None:
    plugin = approved_plugin(tmp_path)
    try:
        for count in range(1, 5):
            with pytest.raises(RACPError) as crashed:
                await plugin.request("re.command", {"action": "crash"}, 5000)
            assert crashed.value.error.code == "PLUGIN_EXITED"
            assert (tmp_path / "side-effects").read_text() == str(count)
            assert plugin.owned is None and plugin.last_cleanup == "complete"
        with pytest.raises(RACPError) as limit:
            await plugin.request("re.command", {"action": "echo"}, 5000)
        assert limit.value.error.code == "CAPABILITY_UNAVAILABLE"
        assert plugin.failure == "PLUGIN_RESTART_LIMIT"
        assert (tmp_path / "side-effects").read_text() == "4"
    finally:
        await plugin.close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_plugin_hang_timeout_or_cancel_terminates_owned_child_and_grandchild(
    tmp_path: Path, cancel: bool
) -> None:
    plugin = approved_plugin(tmp_path)
    handles: list[Any] = []
    try:
        await plugin.start()
        work = asyncio.create_task(
            plugin.request("re.command", {"action": "hang"}, 1000 if not cancel else 5000)
        )
        for _ in range(100):
            if (tmp_path / "descendants.json").exists():
                break
            await asyncio.sleep(0.01)
        pids = json.loads((tmp_path / "descendants.json").read_text())
        births = {pid: psutil.Process(pid).create_time() for pid in pids}
        if os.name == "nt":
            import win32api

            handles = [win32api.OpenProcess(0x101000, False, pid) for pid in pids]
        if cancel:
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(work, 6)
        else:
            with pytest.raises(RACPError) as timed_out:
                await work
            assert timed_out.value.error.code == "TIMEOUT"
            assert timed_out.value.error.execution_state == "unknown"
        assert plugin.owned is None and plugin.last_cleanup == "complete"
        if os.name == "nt":
            import win32event

            assert all(win32event.WaitForSingleObject(handle, 0) == 0 for handle in handles)
        for pid, birth in births.items():
            assert not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != birth
    finally:
        for handle in handles:
            handle.Close()
        await plugin.close()


async def test_plugin_health_mismatch_never_becomes_ready(tmp_path: Path) -> None:
    plugin = approved_plugin(tmp_path, "wrong")
    try:
        with pytest.raises(RACPError) as mismatch:
            await plugin.start()
        assert mismatch.value.error.code == "PLUGIN_SCHEMA_MISMATCH"
        assert plugin.state == "DEGRADED" and plugin.last_cleanup == "complete"
    finally:
        await plugin.close()


async def test_plugin_admission_limit_and_queue_cancellation_preserve_the_active_call(
    tmp_path: Path,
) -> None:
    plugin = approved_plugin(tmp_path)
    calls = []
    try:
        await plugin.start()
        active = asyncio.create_task(plugin.request("re.command", {"action": "hang"}, 10000))
        calls.append(active)
        for _ in range(100):
            if (tmp_path / "descendants.json").exists():
                break
            await asyncio.sleep(0.01)
        assert (tmp_path / "descendants.json").exists()
        queued = [
            asyncio.create_task(plugin.request("re.command", {"action": "echo"}, 10000))
            for _ in range(63)
        ]
        calls.extend(queued)
        await asyncio.sleep(0)
        assert plugin.admitted == 64
        with pytest.raises(RACPError) as full:
            await plugin.request("re.command", {"action": "echo"}, 10000)
        assert full.value.error.code == "RESOURCE_EXHAUSTED"
        for call in queued:
            call.cancel()
        await asyncio.gather(*queued, return_exceptions=True)
        assert plugin.admitted == 1 and plugin.owned is not None and plugin.owned.running()
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(active, 6)
        assert plugin.admitted == 0 and plugin.last_cleanup == "complete"
    finally:
        for call in calls:
            call.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
        await plugin.close()


@pytest.mark.parametrize("cause", ["health_timeout", "startup_cancel"])
async def test_plugin_unresponsive_startup_never_leaves_an_owned_process(
    tmp_path: Path, cause: str
) -> None:
    plugin = approved_plugin(tmp_path, "hang")
    work = asyncio.create_task(plugin.start())
    try:
        if cause == "startup_cancel":
            for _ in range(100):
                if plugin.owned is not None:
                    break
                await asyncio.sleep(0.01)
            assert plugin.owned is not None
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(work, 6)
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(work, 7)
        assert plugin.state == "DEGRADED" and plugin.owned is None
        assert plugin.last_cleanup == "complete"
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        await plugin.close()


async def test_plugin_startup_does_not_overwrite_unverified_native_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from racp_agent.plugins.process import OwnedPluginProcess

    plugin = approved_plugin(tmp_path)

    async def unverified(_: PluginManifest) -> Any:
        raise RACPError(
            "EXECUTION_UNKNOWN",
            "injected native spawn cleanup failure",
            layer="plugin",
            execution_state="unknown",
            cleanup_status="unverified",
        )

    monkeypatch.setattr(OwnedPluginProcess, "start", unverified)
    try:
        with pytest.raises(RACPError) as error:
            await plugin.start()
        assert error.value.error.code == "EXECUTION_UNKNOWN"
        assert plugin.state == "DEGRADED" and plugin.last_cleanup == "unverified"
    finally:
        await plugin.close()
    assert plugin.last_cleanup == "unverified"
