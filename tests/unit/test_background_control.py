import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest
from pydantic import ValidationError
from racp_agent.background import (
    BackgroundRecord,
    ControlRequest,
    launch_arguments,
    launch_digest,
    paths,
    stop,
)
from racp_agent.instance_lock import InstanceLock, InstanceRunningError
from racp_agent.main import parser
from racp_sdk.security import SecretStore, token


def test_instance_lock_rejects_second_process_and_releases_without_unlink(tmp_path: Path) -> None:
    path = tmp_path / "agent.lock"
    script = (
        "import sys; from pathlib import Path; "
        "from racp_agent.instance_lock import InstanceLock; "
        "lock=InstanceLock(Path(sys.argv[1])); lock.__enter__(); print('acquired')"
    )
    with InstanceLock(path):
        with pytest.raises(InstanceRunningError):
            with InstanceLock(path):
                pass
        blocked = subprocess.run(
            [sys.executable, "-c", script, str(path)], capture_output=True, timeout=10
        )
        assert blocked.returncode != 0 and b"InstanceRunningError" in blocked.stderr
    acquired = subprocess.run(
        [sys.executable, "-c", script, str(path)], capture_output=True, timeout=10
    )
    assert acquired.returncode == 0 and b"acquired" in acquired.stdout
    assert path.exists()
    with InstanceLock(path):
        pass  # Child exit releases the OS lock even without an explicit __exit__.


def test_private_control_schema_rejects_invalid_and_unbounded_frames() -> None:
    for bad in ["☃" * 32, "short", "x" * 129]:
        with pytest.raises(ValidationError):
            ControlRequest(action="stop", secret=bad, nonce=token())
    with pytest.raises(ValidationError):
        ControlRequest.model_validate({"action": "execute", "secret": token(), "nonce": token()})
    with pytest.raises(ValidationError):
        BackgroundRecord(
            pid=1,
            created=float("nan"),
            port=1,
            instance_id=token(),
            secret=token(),
            launch_digest="0" * 64,
        )


def test_control_location_separates_credential_files_in_one_folder(tmp_path: Path) -> None:
    assert paths(tmp_path / "credential.bin")[0] == tmp_path / "background"
    assert paths(tmp_path / "first.bin") != paths(tmp_path / "second.bin")
    if os.name == "nt":
        assert paths(tmp_path / "first.bin") == paths(tmp_path / "FIRST.BIN")


def test_lock_uses_canonical_folder_for_windows_short_path_alias(tmp_path: Path) -> None:
    folder = tmp_path
    if os.name == "nt":
        import win32api

        folder = Path(win32api.GetShortPathName(str(tmp_path)))
    with InstanceLock(folder / "alias.lock") as lock:
        assert lock.path == tmp_path.resolve() / "alias.lock"
        with pytest.raises(InstanceRunningError):
            with InstanceLock(tmp_path / "alias.lock"):
                pass


async def test_wrong_control_identity_never_sends_stop_even_for_a_live_recorded_pid(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credential.bin"
    root, record_path = paths(credentials)
    root.mkdir()
    actions = []

    async def fake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        value = json.loads(await reader.readline())
        actions.append(value["action"])
        writer.write(
            json.dumps(
                {
                    "nonce": value["nonce"],
                    "instance_id": record.instance_id,
                    "pid": record.pid + 1,
                    "created": record.created,
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(fake, "127.0.0.1", 0)
    record = BackgroundRecord(
        pid=os.getpid(),
        created=psutil.Process().create_time(),
        port=server.sockets[0].getsockname()[1],
        instance_id=token(),
        secret=token(),
        launch_digest="0" * 64,
    )
    SecretStore(record_path).save({"record": record.model_dump_json()})
    async with server:
        with pytest.raises(PermissionError):
            await stop(credentials)
    assert actions == ["status"]
    assert psutil.Process(record.pid).create_time() == record.created


def test_detached_launch_preserves_explicit_options_and_start_named_paths(tmp_path: Path) -> None:
    root = parser()
    root.add_argument("action")
    args = root.parse_args(
        [
            "start",
            "--credentials",
            str(tmp_path / "start"),
            "--workspace",
            str(tmp_path),
            "--profile",
            "standard",
            "--desktop-session-id",
            "1",
            "--browser-allow-origin",
            "https://example.com",
        ]
    )
    child = root.parse_args(["serve", *launch_arguments(args)])
    assert launch_digest(args, args.credentials) == launch_digest(child, child.credentials)
    assert child.workspace == tmp_path and child.desktop_session_id == [1]
