import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.identity import process_identity
from racp_agent.broker.pipe import Pipe, PipeServer, proof, request

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows local Named Pipe")


@pytest.fixture(autouse=True)
def interactive_user_session() -> None:
    if os.name == "nt" and process_identity(os.getpid()).session == 0:
        pytest.skip("foreground Broker pipe tests require a logged-on Windows session")


def test_broker_actual_pipe_mutual_challenge_acl_and_bounded_idle(tmp_path: Path) -> None:
    identity = process_identity(os.getpid())
    config = BrokerConfig.pair(identity, identity, secrets.token_hex(16))
    path = config.save(tmp_path / "pair")
    assert BrokerConfig.load(path) == config
    server = PipeServer(config)
    stop = threading.Event()
    calls: list[dict[str, object]] = []

    def handler(value: dict[str, object]) -> dict[str, object]:
        calls.append(value)
        return {"accepted": value["operation"]}

    def serve() -> None:
        while not stop.is_set():
            server.accept(handler)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        time.sleep(0.4)  # At least one idle overlapped accept timeout.
        assert request(config, {"operation": "desktop.windows"}) == {"accepted": "desktop.windows"}
        assert len(calls) == 1
        assert proof(config, "broker", "a", "b") != proof(config, "agent", "a", "b")
        assert proof(config, "agent", "a", "b") != proof(config, "agent", "b", "a")
        bad_secret = config.model_copy(update={"secret": "x" * 43})
        time.sleep(0.05)
        with pytest.raises(PermissionError, match="challenge failed"):
            request(bad_secret, {"operation": "desktop.windows"})
        assert len(calls) == 1
        # Kernel pipe ACL has exactly the paired user/Agent, no Everyone/anonymous ACE.
        import win32security

        acl = win32security.GetSecurityInfo(
            server.handle, win32security.SE_KERNEL_OBJECT, win32security.DACL_SECURITY_INFORMATION
        ).GetSecurityDescriptorDacl()
        assert {
            win32security.ConvertSidToStringSid(acl.GetAce(i)[2]) for i in range(acl.GetAceCount())
        } == {identity.sid}
        with pytest.raises(PermissionError):
            config.require_agent(replace(identity, sid="S-1-5-18"))
        with pytest.raises(PermissionError):
            config.require_agent(replace(identity, created=identity.created + 1))
        with pytest.raises(PermissionError):
            config.require_broker(replace(identity, session=identity.session + 1))
        service = config.model_copy(update={"agent_service_sid": "S-1-5-80-1-2-3-4-5"})
        with pytest.raises(PermissionError):
            service.require_agent(identity)
        service.require_agent(replace(identity, service_sids=("S-1-5-80-1-2-3-4-5",)))
    finally:
        stop.set()
        thread.join(5)
        server.close()
    assert not thread.is_alive()


def test_broker_rejects_wrong_paired_pid_and_oversized_message() -> None:
    import win32file

    identity = process_identity(os.getpid())
    config = BrokerConfig.pair(identity, identity, secrets.token_hex(16))
    server = PipeServer(config.model_copy(update={"agent_pid": identity.pid + 100000}))
    calls: list[dict[str, object]] = []
    thread = threading.Thread(target=lambda: server.accept(lambda value: calls.append(value) or {}))
    thread.start()
    pipe = Pipe(
        win32file.CreateFile(config.pipe, 0x100003, 0, None, 3, 0x40000000 | 0x120000, None)
    )
    try:
        pipe.write({"version": 1, "nonce": secrets.token_hex(32)})
        from racp_agent.broker.pipe import NativeError

        with pytest.raises((NativeError, OSError)):
            pipe.read()
        with pytest.raises(ValueError, match="exceeds limit"):
            pipe.write({"data": "x" * 65536})
    finally:
        pipe.close()
        thread.join(5)
        server.close()
    assert not thread.is_alive() and not calls


def test_standalone_broker_process_and_native_session_gate(tmp_path: Path) -> None:
    import ctypes

    import win32pipe
    from racp_agent.broker.windows import Input, WindowsDesktop

    identity = process_identity(os.getpid())
    config = BrokerConfig.pair(identity, identity, secrets.token_hex(16))
    path = config.save(tmp_path / "pair")
    with subprocess.Popen(
        [sys.executable, "-m", "racp_agent.broker.main", "--pairing", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as child:
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                assert child.poll() is None
                try:
                    win32pipe.WaitNamedPipe(config.pipe, 50)
                    break
                except Exception:
                    time.sleep(0.025)
            else:
                pytest.fail("standalone Broker did not open its pipe")
            desktop = WindowsDesktop(identity.session)
            try:
                status = desktop.status()
            finally:
                desktop.close()
            response = request(
                config,
                {
                    "operation": "desktop.monitors",
                    "payload": {"session_id": identity.session},
                    "context": {
                        "owner_id": "owner_test",
                        "device_id": "device_test",
                        "operation_id": "op_test",
                    },
                },
            )
            if status["available"]:
                assert response["state"] == "SUCCEEDED"
                assert response["result"]["monitors"]
            else:
                assert response["state"] == "FAILED"
                assert response["error"]["code"] == status["error_code"]
            assert ctypes.sizeof(Input) == (40 if sys.maxsize > 2**32 else 28)
        finally:
            child.terminate()
            child.wait(5)
