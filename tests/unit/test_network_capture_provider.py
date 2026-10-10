"""Real owned workers verify cancellation/denial/cleanup; fixtures do not emulate raw sockets."""

import asyncio
import hashlib
import importlib
import importlib.util
import os
import struct
import sys
import threading
from pathlib import Path

import psutil
import pytest
from racp_domain.models import ExecutionContext, RACPError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows capture worker containment")


def provider_type():
    assert importlib.util.find_spec("racp_agent.providers.network_capture"), (
        "Capture Provider missing"
    )
    return importlib.import_module("racp_agent.providers.network_capture").NetworkCaptureProvider


def context(timeout: int = 5000) -> ExecutionContext:
    return ExecutionContext(
        "op_capture",
        "req_capture",
        "0" * 32,
        "dev_owned",
        "owner",
        "boot_owned",
        timeout,
        "default",
    )


def payload() -> dict:
    return {
        "local_ip": "192.168.29.121",
        "peer_ip": "192.168.29.141",
        "local_port": 50123,
        "duration_ms": 100,
        "max_bytes": 4096,
    }


def fixture(
    provider,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hang: bool = False,
    content: bytes | None = None,
):
    from racp_agent.plugins.process import ContainedCommand

    packet = bytes.fromhex(
        "450000280001000040060000c0a81d79c0a81d8dc3cb223d00000001000000005010040000000000"
    )
    data = (
        struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101)
        + struct.pack("<IIII", 1, 0, 40, 40)
        + packet
    )
    if content is not None:
        data = content
    script = tmp_path / "owned_capture_worker.py"
    script.write_text(
        "import hashlib,json,sys,time;from pathlib import Path\n"
        f"data={data!r};path=Path(sys.argv[1]);path.write_bytes(data)\n"
        + (
            "time.sleep(30)\n"
            if hang
            else "print(json.dumps({'status':'SUCCEEDED','backend':'owned test fixture',"
            "'local_ip':'192.168.29.121','peer_ip':'192.168.29.141','local_port':50123,"
            "'packets':1,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),"
            "'duration_ms':100,'complete':True,'termination_reason':'duration',"
            "'raw_socket_closed':True,'link_type':'raw_ipv4','scope':'selected_ipv4_tcp_udp_flow',"
            "'non_initial_fragments':'excluded','tls_decryption':False}))\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        provider,
        "command",
        lambda data, path: ContainedCommand(
            command=[sys.executable, "-I", str(script), str(path)], working_directory=str(tmp_path)
        ),
    )
    return data


async def started(provider, spool: Path) -> set[int]:
    for _ in range(100):
        if await asyncio.to_thread(lambda: list(spool.glob("*.pcap"))):
            return provider.protected_pids()
        await asyncio.sleep(0.02)
    pytest.fail("Owned fixture worker never started")


async def test_receipt_matches_actual_spooled_bytes_and_cleanup_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = provider_type()(tmp_path / "spool")
    data = fixture(provider, tmp_path, monkeypatch)
    outcome = await provider.execute(payload(), context(), lambda: None)
    result = outcome["result"]
    path = Path(result["spool_path"])
    assert outcome["state"] == "SUCCEEDED" and await asyncio.to_thread(path.read_bytes) == data
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["cleanup_status"] == "complete" and result["device_id"] == "dev_owned"
    assert not provider.protected_pids()
    provider.release("op_capture", preserve=False)
    assert not await asyncio.to_thread(path.exists)


@pytest.mark.parametrize("reason", ["cancel", "timeout", "revoke"])
async def test_failed_capture_stops_owned_worker_and_never_retains_export_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    spool = tmp_path / "spool"
    provider = provider_type()(spool)
    fixture(provider, tmp_path, monkeypatch, hang=True)
    denied = False

    def gate() -> None:
        if denied:
            raise RACPError("PERMISSION_DENIED", "Fixture revoked")

    task = asyncio.create_task(
        provider.execute(payload(), context(1000 if reason == "timeout" else 5000), gate)
    )
    if reason == "timeout":
        # Startup belongs to the same deadline; a timeout need not create any PCAP.
        pids: set[int] = set()
        async with asyncio.timeout(8):
            while not task.done():
                pids.update(provider.protected_pids())
                await asyncio.sleep(0.02)
    else:
        pids = await started(provider, spool)
        assert pids
    if reason == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
    else:
        denied = reason == "revoke"
        with pytest.raises(RACPError) as raised:
            await asyncio.wait_for(task, 8)
        assert raised.value.error.code == (
            "TIMEOUT" if reason == "timeout" else "PERMISSION_DENIED"
        )
    assert not provider.protected_pids() and not list(spool.glob("*.pcap"))
    assert all(not psutil.pid_exists(pid) for pid in pids)


async def test_cancel_during_final_cleanup_cannot_return_success_or_missing_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from racp_agent.plugins.process import OwnedPluginProcess

    provider = provider_type()(tmp_path / "spool")
    fixture(provider, tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = OwnedPluginProcess.stop

    async def held_stop(owned):
        entered.set()
        await release.wait()
        await original(owned)

    monkeypatch.setattr(OwnedPluginProcess, "stop", held_stop)
    task = asyncio.create_task(provider.execute(payload(), context(), lambda: None))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        assert not provider.pending and not list(provider.spool.glob("*.pcap"))
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_short_udp_record_is_exportable_and_fits_its_exact_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = provider_type()(tmp_path / "spool")
    packet = bytes.fromhex("4500001c0001000040110000c0a81d79c0a81d8dc3cb223d00080000")
    data = (
        struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101)
        + struct.pack("<IIII", 1, 0, 28, 28)
        + packet
    )
    assert len(data) == 68
    fixture(provider, tmp_path, monkeypatch, content=data)
    try:
        outcome = await provider.execute({**payload(), "max_bytes": 68}, context(), lambda: None)
        assert outcome["state"] == "SUCCEEDED" and outcome["result"]["bytes"] == 68
    finally:
        provider.release("op_capture", preserve=False)


async def test_relative_spool_is_canonical_and_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = provider_type()(Path("spool"))
    fixture(provider, tmp_path, monkeypatch)
    try:
        outcome = await provider.execute(payload(), context(), lambda: None)
        assert Path(outcome["result"]["spool_path"]).is_absolute()
        assert outcome["result"]["cleanup_status"] == "complete"
    finally:
        provider.release("op_capture", preserve=False)


async def test_cancel_waits_for_pinned_verification_file_before_deleting_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = provider_type()(tmp_path / "spool")
    fixture(provider, tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = provider.verify

    def held_verify(path, receipt, data):
        with provider.guard.parent(path) as parent:
            with os.fdopen(parent.open(path.name, os.O_RDONLY), "rb"):
                entered.set()
                assert release.wait(8)
        original(path, receipt, data)

    monkeypatch.setattr(provider, "verify", held_verify)
    task = asyncio.create_task(provider.execute(payload(), context(), lambda: None))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "Deletion must wait for the verifier's pinned handle"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        assert not provider.pending and not list(provider.spool.glob("*.pcap"))
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
