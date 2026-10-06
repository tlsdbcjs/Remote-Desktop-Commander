"""Detached user Agent with bounded, authenticated loopback lifecycle control."""

import argparse
import asyncio
import getpass
import hmac
import io
import json
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Literal

import psutil
from pydantic import Field
from racp_protocol.models import StrictModel
from racp_sdk.security import SecretStore, canonical_digest, digest, token

from racp_agent.instance_lock import InstanceLock, InstanceRunningError
from racp_agent.main import open_agent
from racp_agent.main import parser as agent_parser
from racp_agent.runtime import Agent
from racp_agent.settings import default_state_dir, local_path, saved_settings


class BackgroundRecord(StrictModel):
    version: Literal[1] = 1
    pid: int = Field(ge=1)
    created: float = Field(gt=0, allow_inf_nan=False)
    port: int = Field(ge=1, le=65535)
    instance_id: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    secret: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    launch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControlRequest(StrictModel):
    action: Literal["status", "stop"]
    secret: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    nonce: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def paths(credentials: Path) -> tuple[Path, Path]:
    credentials = local_path(credentials.absolute())
    name = os.path.normcase(credentials.name)
    folder = "background" if name == "credential.bin" else "background-" + digest(name)[:16]
    root = local_path(credentials.parent / folder)
    return root, local_path(root / "control.bin")


def load_record(credentials: Path) -> BackgroundRecord | None:
    _, path = paths(credentials)
    try:
        raw = SecretStore(path).load()["record"]
    except FileNotFoundError:
        return None
    return BackgroundRecord.model_validate_json(raw)


def matching_process(record: BackgroundRecord) -> bool:
    try:
        process = psutil.Process(record.pid)
        return (
            process.status() != psutil.STATUS_ZOMBIE
            and process.create_time() == record.created
            and (process.username().casefold() == psutil.Process().username().casefold())
        )
    except psutil.NoSuchProcess:
        return False


def launched_process(record: BackgroundRecord, pid: int, created: float) -> bool:
    if not matching_process(record):
        return False
    try:
        process = psutil.Process(record.pid)
        return any(
            p.pid == pid and p.create_time() == created for p in [process, *process.parents()]
        )
    except psutil.NoSuchProcess:
        return False


def launch_digest(args: argparse.Namespace, credentials: Path) -> str:
    values = vars(args).copy()
    values.pop("action", None)
    values["credentials"] = str(credentials.absolute())
    return canonical_digest(json.loads(json.dumps(values, default=str)))


async def control(record: BackgroundRecord, action: Literal["status", "stop"]) -> dict[str, Any]:
    if not matching_process(record):
        raise ProcessLookupError("Recorded Agent instance has exited")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", record.port, limit=16384), 2
    )
    nonce = token()
    try:
        request = ControlRequest(action=action, secret=record.secret, nonce=nonce)
        writer.write(request.model_dump_json().encode() + b"\n")
        await asyncio.wait_for(writer.drain(), 2)
        raw = await asyncio.wait_for(reader.readline(), 2)
        if not raw or len(raw) > 16384:
            raise ValueError("Invalid local control response")
        value = json.loads(raw)
        if not isinstance(value, dict) or (
            value.get("nonce") != nonce
            or value.get("instance_id") != record.instance_id
            or value.get("pid") != record.pid
            or value.get("created") != record.created
        ):
            raise PermissionError("Local Agent identity mismatch")
        value.pop("nonce")
        value.pop("instance_id")
        return value
    finally:
        writer.close()
        await writer.wait_closed()


def snapshot(agent: Agent, record: BackgroundRecord, *, stopping: bool) -> dict[str, Any]:
    connected = bool(
        agent.socket is not None
        and agent.connection_phase == "ready"
        and agent.lease_expires > time.monotonic()
        and not stopping
    )
    desktop = agent.desktop.capability()
    return {
        "state": "STOPPING" if stopping else "RUNNING",
        "pid": record.pid,
        "created": record.created,
        "instance_id": record.instance_id,
        "device_id": agent.device_id,
        "agent_boot_id": agent.boot_id,
        "execution_identity": getpass.getuser(),
        "profile": agent.profile,
        "connected": connected,
        "connection_epoch": agent.epoch,
        "connection_phase": agent.connection_phase if agent.socket is not None else "disconnected",
        "workspaces": agent.filesystem.guard.inventory(),
        "active_operations": len(agent.tasks),
        "desktop": {
            "enabled": desktop.enabled,
            "healthy": desktop.healthy,
            "unavailable_reason": desktop.unavailable_reason,
            "sessions": [
                {
                    "session_id": item["session_id"],
                    "available": bool(item.get("available")),
                    "input_ready": bool(item.get("input_guardian_available")),
                }
                for item in desktop.attributes.get("sessions", [])
            ],
        },
        "operations": [
            {
                "operation_id": operation_id,
                "operation": record["request"]["operation"],
                "state": record["state"],
            }
            for operation_id in list(agent.tasks)[:16]
            for record in [agent.journal.get(operation_id)]
        ],
    }


async def serve(credentials: Path, args: argparse.Namespace) -> None:
    root, record_path = paths(credentials)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    local_path(root)
    log_path = local_path(root / "agent.log")
    handler = RotatingFileHandler(log_path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(message)s", force=True)
    try:
        with open_agent(credentials, args) as agent:
            task: asyncio.Task[None] | None = None
            stopping = False
            clients = 0
            record: BackgroundRecord

            async def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                nonlocal stopping, clients
                clients += 1
                accepted_stop = False
                try:
                    if clients > 8:
                        return
                    raw = await asyncio.wait_for(reader.readline(), 2)
                    if len(raw) > 4096:
                        return
                    request = ControlRequest.model_validate_json(raw)
                    if not hmac.compare_digest(request.secret, record.secret):
                        return
                    if request.action == "stop":
                        stopping = True
                        accepted_stop = True
                    writer.write(
                        json.dumps(
                            {
                                **snapshot(agent, record, stopping=stopping),
                                "nonce": request.nonce,
                            },
                            ensure_ascii=False,
                        ).encode()
                        + b"\n"
                    )
                    await asyncio.wait_for(writer.drain(), 2)
                except (OSError, ValueError, TimeoutError):
                    pass
                finally:
                    if accepted_stop:
                        agent.stopping.set()
                        if task is not None and not task.cancelling():
                            task.cancel()
                    clients -= 1
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(accepted, "127.0.0.1", 0, limit=4096)
            record = BackgroundRecord(
                pid=os.getpid(),
                created=psutil.Process().create_time(),
                port=server.sockets[0].getsockname()[1],
                instance_id=token(),
                secret=token(),
                launch_digest=launch_digest(args, credentials),
            )
            try:
                SecretStore(record_path).save({"record": record.model_dump_json()})
                task = asyncio.create_task(agent.run())
                async with server:
                    try:
                        await task
                    except asyncio.CancelledError:
                        if not stopping:
                            raise
            finally:
                agent.stopping.set()
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                if agent.cleanup_complete:
                    SecretStore(local_path(root / "shutdown.bin")).save(
                        {
                            "instance_id": record.instance_id,
                            "cleanup_status": "complete",
                        }
                    )
                # Only this instance may retire its metadata; no PID kill fallback.
                current = load_record(credentials)
                if current is not None and current.instance_id == record.instance_id:
                    record_path.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, InstanceRunningError) as exc:
        logging.error("Background Agent could not start: %s", type(exc).__name__)
        raise
    finally:
        handler.close()


async def status(credentials: Path) -> dict[str, Any]:
    record = load_record(credentials)
    if record is None or not matching_process(record):
        return {"state": "STOPPED", "connected": False}
    return await control(record, "status")


def preflight(credentials: Path) -> None:
    saved_settings(SecretStore(local_path(credentials)).load())


def launch_arguments(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    for name in (
        "credentials",
        "workspace",
        "data_dir",
        "ca_file",
        "plugin_config",
        "profile",
        "service_sid",
    ):
        value = getattr(args, name)
        if value is not None:
            values.extend(["--" + name.replace("_", "-"), str(value)])
    for name in ("desktop_session_id", "desktop_login_user_sid", "browser_allow_origin"):
        for value in getattr(args, name):
            values.extend(["--" + name.replace("_", "-"), str(value)])
    for workspace in args.allow_workspace or []:
        values.extend(["--allow-workspace", f"{workspace.id}={workspace.path}"])
    if args.enable_browser_cdp:
        values.append("--enable-browser-cdp")
    if args.enable_desktop:
        values.append("--enable-desktop")
    return values


def spawn(arguments: list[str]) -> subprocess.Popen[bytes]:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    return subprocess.Popen(
        [sys.executable, "-m", "racp_agent.background", "serve", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=os.name != "nt",
        creationflags=flags,
    )


async def start(
    credentials: Path, args: argparse.Namespace, arguments: list[str]
) -> dict[str, Any]:
    root, _ = paths(credentials)
    with InstanceLock(root / "controller.lock"):
        record = load_record(credentials)
        if record is not None and matching_process(record):
            if record.launch_digest != launch_digest(args, credentials):
                raise InstanceRunningError("Stop the existing Agent before changing launch options")
            return await control(record, "status")
        await asyncio.to_thread(preflight, credentials)
        child = await asyncio.to_thread(spawn, arguments)
        created = psutil.Process(child.pid).create_time()
        # Retain no child process handle after launch; Agent owns its own lifetime.
        for _ in range(300):
            record = load_record(credentials)
            if record is not None and launched_process(record, child.pid, created):
                return await control(record, "status")
            if child.poll() is not None:
                raise RuntimeError("Background Agent did not start; inspect its local log")
            await asyncio.sleep(0.05)
        raise TimeoutError("Agent start is unconfirmed; inspect status before retrying")


async def stop(credentials: Path) -> dict[str, Any]:
    root, _ = paths(credentials)
    with InstanceLock(root / "controller.lock"):
        record = load_record(credentials)
        if record is None or not matching_process(record):
            return {"state": "STOPPED", "connected": False}
        await control(record, "status")
        await control(record, "stop")
        for _ in range(400):
            if not matching_process(record):
                receipt_path = local_path(root / "shutdown.bin")
                try:
                    receipt = SecretStore(receipt_path).load()
                except FileNotFoundError:
                    receipt = {}
                complete = (
                    receipt.get("instance_id") == record.instance_id
                    and receipt.get("cleanup_status") == "complete"
                )
                return {
                    "state": "STOPPED",
                    "connected": False,
                    "cleanup_status": "complete" if complete else "unknown",
                }
            await asyncio.sleep(0.05)
        raise TimeoutError("Agent cleanup is unconfirmed; inspect status, no forced kill was sent")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = agent_parser()
    parser.description = "RACP user Agent background lifecycle controller"
    parser.add_argument("action", choices=["start", "status", "stop", "serve"])
    args = parser.parse_args()
    credentials = (
        args.credentials
        or (
            Path(".racp/agent/credential.bin")
            if args.workspace
            else default_state_dir() / "credential.bin"
        )
    ).absolute()
    args.credentials = credentials
    for name in ("workspace", "data_dir", "ca_file", "plugin_config"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.absolute())
    try:
        if args.action == "serve":
            asyncio.run(serve(credentials, args))
            return
        arguments = launch_arguments(args)
        result = asyncio.run(
            start(credentials, args, arguments)
            if args.action == "start"
            else status(credentials)
            if args.action == "status"
            else stop(credentials)
        )
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, RuntimeError, psutil.Error) as exc:
        print(
            f"Agent lifecycle request failed ({type(exc).__name__}); "
            "inspect local configuration, status and agent.log",
            file=sys.stderr,
        )
        raise SystemExit(4) from None


if __name__ == "__main__":
    main()
