import asyncio
import json
import os
import subprocess
import sys
import uuid
from typing import Any

import psutil
from racp_agent.background import load_record, paths
from racp_agent.connect import credential_document
from racp_agent.settings import AgentSettings
from racp_sdk.security import SecretStore, token


async def test_detached_agent_reconnects_rejects_duplicates_and_stops_owned_work(
    live: dict[str, Any],
) -> None:
    http = live["client"]
    issued = (await http.post("/api/v1/enrollment-tokens", json={"name": "Background PC"})).json()
    enrolled = (await http.post("/agent/v1/enroll", json={"token": issued["token"]})).json()
    root = live["workspace"].parent / "background-pc"
    root.mkdir()
    workspace = root / "자료"
    workspace.mkdir()
    credentials = root / "credential.bin"
    settings = AgentSettings(
        gateway=live["url"],
        device_id=enrolled["device_id"],
        workspace=workspace,
        data_dir=root / "data",
    )
    SecretStore(credentials).save(credential_document(settings, enrolled["credential"]))
    prefix = [sys.executable, "-m", "racp_agent.background"]
    original_record = None

    async def cli(action: str, *options: str, expected: int = 0) -> Any:
        completed = await asyncio.to_thread(
            subprocess.run,
            [*prefix, action, "--credentials", str(credentials), *options],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=40,
        )
        assert completed.returncode == expected, completed.stderr
        assert enrolled["credential"] not in completed.stdout + completed.stderr
        assert live["owner"] not in completed.stdout + completed.stderr
        return json.loads(completed.stdout) if expected == 0 else completed.stderr

    async def connected(previous_epoch: int = 0) -> Any:
        for _ in range(300):
            result = (await http.get("/api/v1/devices/" + enrolled["device_id"])).json()
            if result["info"].get("status") == "ONLINE" and result["epoch"] > previous_epoch:
                return result
            await asyncio.sleep(0.05)
        raise AssertionError("Background Agent did not reconcile")

    async def remote(name: str, payload: dict[str, Any], **extra: Any) -> Any:
        response = await http.post(
            "/api/v1/operations",
            json={
                "device_id": enrolled["device_id"],
                "operation": name,
                "payload": payload,
                "idempotency_key": uuid.uuid4().hex,
                "execution_profile_id": "trusted_personal",
                **extra,
            },
        )
        assert response.status_code in {200, 202}, response.text
        return response.json()

    try:
        started = await cli("start", "--profile", "trusted_personal")
        original_record = load_record(credentials)
        assert original_record is not None and started["pid"] == original_record.pid
        online = await connected()
        observation = await cli("status")
        assert (
            observation["connected"]
            and observation["agent_boot_id"] == online["info"]["agent_boot_id"]
        )
        repeated = await cli("start", "--profile", "trusted_personal")
        assert repeated["pid"] == started["pid"]
        await cli("start", "--profile", "read_only", expected=4)
        _, control_path = paths(credentials)
        # Stale/forged creation time cannot cause a PID kill or stop request.
        forged = original_record.model_copy(update={"created": original_record.created + 1000})
        SecretStore(control_path).save({"record": forged.model_dump_json()})
        assert (await cli("stop"))["state"] == "STOPPED"
        assert psutil.Process(original_record.pid).create_time() == original_record.created
        SecretStore(control_path).save({"record": original_record.model_dump_json()})
        reader, writer = await asyncio.open_connection("127.0.0.1", original_record.port)
        writer.write(
            json.dumps({"action": "stop", "secret": token(), "nonce": token()}).encode() + b"\n"
        )
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 3) == b""
        writer.close()
        await writer.wait_closed()
        assert (await cli("status"))["connected"]
        await live["restart_gateway"]()
        recovered = await connected(online["epoch"])
        assert recovered["info"]["agent_boot_id"] == observation["agent_boot_id"]
        result = await remote(
            "filesystem.write", {"path": "background.txt", "content": "원격 자료"}
        )
        assert (
            result["state"] == "SUCCEEDED"
            and (workspace / "background.txt").read_text(encoding="utf-8") == "원격 자료"
        )
        code = (
            "import os,time; from pathlib import Path; "
            "Path('job.pid').write_text(str(os.getpid())); time.sleep(90)"
        )
        job = await remote(
            "shell.exec", {"argv": [sys.executable, "-c", code]}, execution_mode="job"
        )
        for _ in range(100):
            if (workspace / "job.pid").exists():
                break
            await asyncio.sleep(0.05)
        child_pid = int((workspace / "job.pid").read_text())
        duplicate = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "racp_agent.main",
                "--credentials",
                str(credentials),
                "--data-dir",
                str(root / "different-data"),
            ],
            capture_output=True,
            timeout=15,
        )
        assert duplicate.returncode == 4
        still_running = (await http.get("/api/v1/operations/" + job["operation_id"])).json()
        assert still_running["state"] == "RUNNING", still_running
        stopped = await cli("stop")
        assert stopped["state"] == "STOPPED" and stopped["cleanup_status"] == "complete"
        assert not psutil.pid_exists(child_pid)
        assert (await cli("status"))["state"] == "STOPPED"
        assert (await cli("stop"))["state"] == "STOPPED"
        restarted = await cli("start", "--profile", "trusted_personal")
        assert restarted["created"] != started["created"] or restarted["pid"] != started["pid"]
        await connected(recovered["epoch"])
        outcome = (await http.get("/api/v1/operations/" + job["operation_id"])).json()
        assert outcome["state"] == "CANCELLED", outcome
        if os.name == "nt":
            crash_key = "background-crash-once"
            crash_code = (
                "import os,time; from pathlib import Path; p=Path('counter.txt'); "
                "p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); "
                "Path('crash.pid').write_text(str(os.getpid())); time.sleep(90)"
            )
            crashed_job = await remote(
                "shell.exec",
                {"argv": [sys.executable, "-c", crash_code]},
                execution_mode="job",
                idempotency_key=crash_key,
            )
            for _ in range(100):
                if (workspace / "crash.pid").exists():
                    break
                await asyncio.sleep(0.05)
            owned_child = int((workspace / "crash.pid").read_text())
            running_record = load_record(credentials)
            assert running_record is not None
            owned_daemon = psutil.Process(running_record.pid)
            assert owned_daemon.create_time() == running_record.created
            owned_daemon.kill()  # Only this test's verified, detached Agent.
            for _ in range(100):
                if not psutil.pid_exists(owned_child):
                    break
                await asyncio.sleep(0.05)
            assert not psutil.pid_exists(owned_child)
            assert (await cli("status"))["state"] == "STOPPED"
            assert (await cli("start", "--profile", "trusted_personal"))[
                "pid"
            ] != running_record.pid
            await connected(recovered["epoch"] + 1)
            repeated = await remote(
                "shell.exec",
                {"argv": [sys.executable, "-c", crash_code]},
                execution_mode="job",
                idempotency_key=crash_key,
            )
            assert repeated["operation_id"] == crashed_job["operation_id"]
            uncertain = (await http.get("/api/v1/operations/" + crashed_job["operation_id"])).json()
            assert uncertain["state"] == "UNKNOWN", uncertain
            assert (workspace / "counter.txt").read_text() == "1"
        assert (await cli("stop"))["cleanup_status"] == "complete"
        log = (root / "background/agent.log").read_text(encoding="utf-8")
        assert original_record.secret not in log and enrolled["credential"] not in log
        assert live["owner"] not in log and '"event": "agent_connected"' in log
    finally:
        if original_record is not None:
            current = load_record(credentials)
            if current is not None and current.created == original_record.created:
                SecretStore(paths(credentials)[1]).save(
                    {"record": original_record.model_dump_json()}
                )
        await cli("stop")
