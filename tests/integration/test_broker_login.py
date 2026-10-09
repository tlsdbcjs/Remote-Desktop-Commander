import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from racp_agent.broker.identity import process_identity
from racp_agent.broker.login_registration import LoginEndpoint, LoginHello, LoginServer
from racp_agent.providers.desktop import DesktopProvider

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows local login registration")


@pytest.fixture(autouse=True)
def user_session() -> None:
    if os.name == "nt" and process_identity(os.getpid()).session == 0:
        pytest.skip("test requires a logged-on user session; SCM identity gate is separate")


async def test_user_login_registration_actual_pipe_job_lifetime_and_descriptor(
    tmp_path: Path,
) -> None:
    actor = process_identity(os.getpid())
    spool = tmp_path / "spool"
    spool.mkdir()
    provider = DesktopProvider(tmp_path / "brokers", spool, "dev_login", login_users=(actor.sid,))
    await provider.start()
    assert provider.registrar is not None and provider.login_error is None
    worker = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            "-I",
            "-m",
            "racp_agent.broker.login",
            "--endpoint-file",
            str(provider.endpoint_path),
            "--once",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        for _ in range(200):
            broker = provider.sessions.get(actor.session)
            if broker is not None and broker.process is not None:
                break
            if worker.poll() is not None:
                pytest.fail(
                    "login worker exited: "
                    + (worker.stderr.read().decode(errors="replace") if worker.stderr else "")
                )
            await asyncio.sleep(0.025)
        else:
            pytest.fail("login Broker was not adopted")
        assert broker is not None and broker.login_managed and broker.config is not None
        assert broker.config.user_sid == actor.sid and broker.config.agent_pid == actor.pid
        assert broker.job is not None
        # Adoption publishes the peer before the worker finishes its guardian bootstrap.
        # Exercise the five-second RPC budget only after actual IPC/guardian readiness.
        ready_deadline = asyncio.get_running_loop().time() + 15
        while broker.guardian is None:
            await provider.probe()
            if worker.poll() is not None:
                pytest.fail("owned login Broker exited during guardian initialization")
            if asyncio.get_running_loop().time() >= ready_deadline:
                pytest.fail("owned login Broker guardian did not become ready")
            await asyncio.sleep(0.025)
        status = await broker.rpc(
            "broker.status", {}, broker.private_context(), asyncio.get_running_loop().time() + 5
        )
        assert status["session_id"] == actor.session and status["user_sid"] == actor.sid
        assert status["input_guardian_available"] and broker.guardian is not None
        assert broker.guardian.config.agent_pid == broker.process.pid
        assert broker.guardian.config.job_name == broker.config.job_name
        guardian_pid = broker.guardian.peer.pid
        cleanup_directory = broker.guardian.config.cleanup_directory
        descriptor = provider.endpoint_path.read_text(encoding="utf-8")
        assert "secret" not in descriptor and "credential" not in descriptor
        endpoint = LoginEndpoint.model_validate_json(descriptor)
        assert endpoint.device_id == "dev_login" and endpoint.agent_sid == actor.sid
        await provider.cleanup()
        await asyncio.to_thread(worker.wait, 5)
        assert broker.process is None and broker.cleanup_status == "complete"
        import psutil

        assert not psutil.pid_exists(guardian_pid)
        if cleanup_directory is not None:
            assert not await asyncio.to_thread(Path(cleanup_directory).exists)
        await provider.probe()
        assert actor.session not in provider.sessions
    finally:
        await provider.shutdown()
        if worker.poll() is None:
            worker.terminate()
            await asyncio.to_thread(worker.wait, 5)
        if worker.stderr is not None:
            worker.stderr.close()


def test_login_rejects_system_wrong_sid_session_device_and_version(tmp_path: Path) -> None:
    actor = process_identity(os.getpid())
    endpoint = LoginEndpoint(device_id="dev_scope", agent_sid=actor.sid)
    server = LoginServer(endpoint, (actor.sid,))
    hello = LoginHello(version=1, nonce="a" * 64, device_id="dev_scope", session_id=actor.session)
    peer = replace(actor, pid=actor.pid + 1)
    try:
        server.validate_peer(hello, peer)
        for wrong in [
            replace(peer, sid="S-1-5-18"),
            replace(peer, session=0),
            replace(peer, session=actor.session + 1),
            actor,
        ]:
            with pytest.raises(PermissionError):
                server.validate_peer(hello, wrong)
        with pytest.raises(PermissionError):
            server.validate_peer(hello.model_copy(update={"device_id": "dev_other"}), peer)
        with pytest.raises(PermissionError):
            LoginEndpoint(device_id="dev_scope", agent_sid="S-1-5-18").check_agent(
                replace(actor, sid="S-1-5-18")
            )
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LoginHello(version=2, nonce="a" * 64, device_id="dev_scope", session_id=actor.session)
        with pytest.raises(ValidationError):
            LoginHello.model_validate({**hello.model_dump(), "token": "caller-supplied-token"})
    finally:
        server.close()


@pytest.mark.desktop_login
async def test_registered_broker_capability_recovery_through_actual_agent_wire(
    live: dict[str, Any],
) -> None:
    import win32job

    actor = process_identity(os.getpid())
    provider = live["agent"].desktop
    worker = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            "-I",
            "-m",
            "racp_agent.broker.login",
            "--endpoint-file",
            str(provider.endpoint_path),
            "--once",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        for _ in range(400):
            device = (await live["client"].get("/api/v1/devices/" + live["device_id"])).json()
            desktop = next(
                cap for cap in device["info"]["capabilities"] if cap["name"] == "desktop"
            )
            if desktop["healthy"]:
                break
            assert worker.poll() is None
            await asyncio.sleep(0.025)
        else:
            pytest.fail("registered capability was not observed on the actual wire")
        assert device["info"]["status"] == "ONLINE"
        response = await live["client"].post(
            "/api/v1/operations",
            json={
                "device_id": live["device_id"],
                "operation": "desktop.sessions",
                "payload": {},
            },
        )
        assert response.status_code == 200 and response.json()["state"] == "SUCCEEDED"
        session = response.json()["result"]["sessions"][0]
        assert session["user_sid"] == actor.sid and session["session_id"] == actor.session
        broker = provider.sessions[actor.session]
        assert (
            win32job.QueryInformationJobObject(
                broker.job, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]
            > 0
        )
        await provider.cleanup()
        await asyncio.to_thread(worker.wait, 5)
        assert broker.cleanup_status == "complete"
    finally:
        if worker.poll() is None:
            worker.terminate()
            await asyncio.to_thread(worker.wait, 5)


async def test_logon_launcher_survives_broker_cleanup_and_registers_fresh_process(
    tmp_path: Path,
) -> None:
    actor = process_identity(os.getpid())
    spool = tmp_path / "spool"
    spool.mkdir()
    provider = DesktopProvider(tmp_path / "brokers", spool, "dev_restart", login_users=(actor.sid,))
    await provider.start()
    launcher = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            "-I",
            "-m",
            "racp_agent.broker.login",
            "--endpoint-file",
            str(provider.endpoint_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        for _ in range(300):
            first = provider.sessions.get(actor.session)
            if first and first.process is not None and first.config is not None:
                break
            await asyncio.sleep(0.025)
        else:
            pytest.fail("launcher did not register its worker")
        first_pid, first_pair = first.process.pid, first.config.pair_id
        await provider.cleanup()
        assert launcher.poll() is None
        for _ in range(400):
            fresh = provider.sessions.get(actor.session)
            if (
                fresh
                and fresh.process is not None
                and fresh.config is not None
                and fresh.config.pair_id != first_pair
            ):
                break
            await asyncio.sleep(0.025)
        else:
            pytest.fail("launcher did not replace the terminated worker")
        assert fresh.process.pid != first_pid and fresh.config.agent_pid == actor.pid
    finally:
        import psutil

        try:
            owned = [
                (p.pid, p.create_time())
                for p in psutil.Process(launcher.pid).children(recursive=True)
            ]
        except psutil.NoSuchProcess:
            owned = []
        await provider.shutdown()
        if launcher.poll() is None:
            launcher.terminate()
            await asyncio.to_thread(launcher.wait, 5)
        for pid, created in owned:
            try:
                process = psutil.Process(pid)
                if process.create_time() == created:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)
            except psutil.NoSuchProcess:
                pass
