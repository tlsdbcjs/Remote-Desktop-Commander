"""Isolated browser contexts with an OS-contained worker per browser Handle."""

import asyncio
import json
import os
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import playwright
import psutil
from racp_agent.providers import browser_cdp
from racp_agent.providers.browser_workspace import BrowserWorkspace
from racp_agent.providers.containment import kill_group
from racp_agent.providers.shell import execution_env
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import new_id, timestamp


@dataclass
class BrowserSession:
    id: str
    context: ExecutionContext
    process: asyncio.subprocess.Process
    job: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: str = field(default_factory=timestamp)
    last_access_at: str = field(default_factory=timestamp)
    expires_at: str = ""
    expires: float = 0
    revision: int = 1
    state: str = "CREATING"
    version: str = ""
    close_result: dict[str, Any] | None = None
    close_task: asyncio.Task[dict[str, Any]] | None = None
    cleanup_status: str = "complete"
    directory: Path | None = None
    quota_task: asyncio.Task[None] | None = None
    failure_code: str | None = None
    disk_bytes: int = 0
    download_limit: int | None = None
    cdp_scope: dict[str, Any] | None = None
    ownership: str = "racp_owned"
    responses: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=lambda: asyncio.Queue(2)
    )
    receiver: asyncio.Task[None] | None = None
    worker_sequence: int = 0

    def touch(self) -> None:
        self.last_access_at = timestamp()
        self.expires = time.monotonic() + 3600
        self.expires_at = (
            (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        )


class BrowserProvider:
    def __init__(
        self, spool: Path, *, allowed_origins: tuple[str, ...] = (), cdp_enabled: bool = False
    ) -> None:
        self.spool = spool
        self.instance_id = new_id("provider")
        self.sessions: dict[str, BrowserSession] = {}
        self.open_lock = asyncio.Lock()
        self.allowed_origins = tuple(self.origin(value) for value in allowed_origins)
        self.workspace = BrowserWorkspace(spool.parent / "browsers")
        self.temporary_cleanup = self.workspace.collect()
        self.disk_limit_bytes = 10 * 1024**3
        self.cdp_enabled = cdp_enabled
        self.on_event: Callable[[BrowserSession, str, str], None] | None = None

    def health(self) -> dict[str, Any]:
        driver = Path(playwright.__file__).parent / "driver"
        system = platform.system()
        installed = (driver / ("node.exe" if system == "Windows" else "node")).is_file()
        native = False
        try:
            browsers = json.loads((driver / "package/browsers.json").read_text(encoding="utf-8"))[
                "browsers"
            ]
            revision = next(
                item["revision"] for item in browsers if item["name"] == "chromium-headless-shell"
            )
            configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            cache = (
                Path(configured)
                if configured and configured != "0"
                else driver / "package/.local-browsers"
                if configured == "0"
                else (
                    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
                    / "ms-playwright"
                    if system == "Windows"
                    else Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
                    / "ms-playwright"
                )
            )
            executable = (
                "chrome-headless-shell-win64/chrome-headless-shell.exe"
                if system == "Windows"
                else "chrome-headless-shell-linux64/chrome-headless-shell"
            )
            native = (cache / ("chromium_headless_shell-" + str(revision)) / executable).is_file()
        except (OSError, ValueError, KeyError, StopIteration):
            pass
        return {
            "installed": installed,
            "supported": system in {"Windows", "Linux"}
            and platform.machine().lower() in {"amd64", "x86_64"},
            "native_browser_available": native,
            "probe": "pinned_runtime_files",
            "runtime_execution": "verified_on_open",
        }

    @staticmethod
    def origin(value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("browser allow-origin requires an HTTP(S) origin")
        host = parsed.hostname.encode("idna").decode().lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parsed.port
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            host += f":{port}"
        return f"{parsed.scheme}://{host}"

    def handle(self, session: BrowserSession, page_id: str | None = None) -> dict[str, Any]:
        page = session.pages.get(page_id) if page_id else None
        state = "CLOSED" if page and page["closed"] else session.state
        return {
            "id": page_id or session.id,
            "type": "browser-page" if page_id else "browser",
            "device_id": session.context.device_id,
            "owner": session.context.principal_id,
            "agent_boot_id": session.context.agent_boot_id,
            "provider_instance_id": self.instance_id,
            "resource_revision": str(page["revision"] if page else session.revision),
            "created_at": page["created_at"] if page else session.created_at,
            "last_access_at": session.last_access_at,
            "expires_at": session.expires_at,
            "state": state,
            "availability": "available" if state == "ACTIVE" else "unavailable",
            "ownership": page.get("ownership", session.ownership) if page else session.ownership,
        }

    def inventory(self) -> list[dict[str, Any]]:
        return [
            handle
            for session in list(self.sessions.values())
            for handle in [
                self.handle(session),
                *[self.handle(session, id) for id in session.pages],
            ]
        ]

    def observe(self, session: BrowserSession, pages: list[dict[str, Any]]) -> None:
        for page in pages:
            id = page["page_id"]
            if id not in session.pages:
                session.pages[id] = {**page, "created_at": timestamp(), "revision": 1}
                session.revision += 1
            elif any(
                session.pages[id][key] != page[key] for key in ("closed", "navigation_revision")
            ):
                session.pages[id].update(page)
                session.pages[id]["revision"] += 1

    def identity(self, payload: dict[str, Any], context: ExecutionContext) -> BrowserSession:
        session = self.sessions.get(payload["browser_id"])
        if session is None or payload.get("agent_boot_id") not in {None, context.agent_boot_id}:
            raise RACPError(
                "HANDLE_EXPIRED", "browser belongs to a previous Agent", layer="provider"
            )
        if (
            session.context.principal_id != context.principal_id
            or session.context.device_id != context.device_id
        ):
            raise RACPError("PERMISSION_DENIED", "browser owner/Device mismatch", layer="provider")
        if session.context.agent_boot_id != context.agent_boot_id:
            raise RACPError("HANDLE_EXPIRED", "Agent boot changed", layer="provider")
        if "page_id" in payload and payload["page_id"] not in session.pages:
            raise RACPError(
                "HANDLE_EXPIRED", "page does not belong to this browser", layer="provider"
            )
        return session

    async def response(self, session: BrowserSession) -> dict[str, Any]:
        try:
            value = await session.responses.get()
            if value is None:
                if session.failure_code:
                    raise RACPError(
                        session.failure_code,
                        "browser resource limit exceeded",
                        layer="provider",
                        execution_state="unknown",
                        cleanup_status=session.cleanup_status,
                    )
                raise ValueError("worker ended")
        except (ValueError, json.JSONDecodeError) as exc:
            raise RACPError(
                "EXECUTION_UNKNOWN",
                "browser worker ended; inspect before replay",
                layer="provider",
                execution_state="unknown",
            ) from exc
        if value.get("error"):
            code = value["error"]["code"]
            raise RACPError(
                code,
                "browser operation rejected",
                layer="provider",
                execution_state=value["error"].get(
                    "execution_state",
                    "unknown" if code in {"TIMEOUT", "BROWSER_ERROR"} else "not_started",
                ),
            )
        self.observe(session, value.get("pages", value["result"].get("pages", [])))
        return dict(value["result"])

    def emit(self, session: BrowserSession, kind: str, state: str) -> None:
        if self.on_event is not None:
            self.on_event(session, kind, state)

    async def receive(self, session: BrowserSession) -> None:
        assert session.process.stdout is not None
        try:
            while line := await session.process.stdout.readline():
                message = json.loads(line)
                if message.get("type") == "event":
                    sequence = int(message["sequence"])
                    if sequence <= session.worker_sequence:
                        continue
                    session.worker_sequence = sequence
                    self.observe(session, message["pages"])
                    self.emit(session, message["kind"], message["state"])
                else:
                    session.responses.put_nowait(message)
        except (ValueError, KeyError, TypeError, asyncio.QueueFull):
            session.failure_code = "EXECUTION_UNKNOWN"
        finally:
            if not session.responses.full():
                session.responses.put_nowait(None)
            if session.state == "ACTIVE":
                session.failure_code = session.failure_code or "EXECUTION_UNKNOWN"
                asyncio.create_task(self.close(session, state="FAILED"))

    async def open(
        self, data: dict[str, Any], context: ExecutionContext, deadline: float
    ) -> dict[str, Any]:
        async with self.open_lock:
            await self.cleanup(expired_only=True)
            if (
                sum(s.state in {"ACTIVE", "CREATING"} for s in self.sessions.values()) >= 4
                or len(self.sessions) >= 12
            ):
                raise RACPError(
                    "RESOURCE_EXHAUSTED", "browser context/history limit reached", layer="provider"
                )
            data = dict(data)
            if "endpoint_url" in data:
                if not self.cdp_enabled:
                    raise RACPError(
                        "PERMISSION_DENIED", "CDP attach disabled locally", layer="provider"
                    )
                data["cdp_endpoint"] = await browser_cdp.resolve(data["endpoint_url"])
            id = new_id("browser")
            directory = self.workspace.create(id)
            env = execution_env({})
            for key in ("TEMP", "TMP", "TMPDIR"):
                env[key] = str(directory / "temp")
            env["PYTHONIOENCODING"] = "utf-8"
            if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
                env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
            kwargs: dict[str, Any] = (
                {"start_new_session": True}
                if os.name != "nt"
                else {"creationflags": getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)}
            )
            # The base interpreter runs a stdlib-only gate before importing Playwright.
            spawning = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    getattr(sys, "_base_executable", sys.executable),
                    "-I",
                    str(Path(__file__).with_name("browser_worker.py")),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=1024**2,
                    env=env,
                    **kwargs,
                )
            )
            interrupted = False
            while True:
                try:
                    process = await asyncio.shield(spawning)
                    break
                except asyncio.CancelledError:
                    if spawning.cancelled():
                        self.workspace.remove(directory)
                        raise
                    interrupted = True
                except BaseException:
                    self.workspace.remove(directory)
                    raise
            session = BrowserSession(id, context, process, directory=directory)
            self.sessions[session.id] = session
            session.receiver = asyncio.create_task(self.receive(session))
            session.touch()
            try:
                self.workspace.mark(directory, process.pid)
                if interrupted:
                    raise asyncio.CancelledError
                if os.name == "nt":
                    import win32api
                    import win32con
                    import win32job

                    session.job = win32job.CreateJobObject(None, "")
                    info = win32job.QueryInformationJobObject(
                        session.job, win32job.JobObjectExtendedLimitInformation
                    )
                    info["BasicLimitInformation"]["LimitFlags"] = (
                        win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    )
                    win32job.SetInformationJobObject(
                        session.job, win32job.JobObjectExtendedLimitInformation, info
                    )
                    handle = win32api.OpenProcess(
                        win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid
                    )
                    try:
                        win32job.AssignProcessToJobObject(session.job, handle)
                    finally:
                        handle.Close()
                assert process.stdin is not None
                process.stdin.write(
                    json.dumps(
                        {
                            **data,
                            "site_packages": str(Path(playwright.__file__).parent.parent),
                            "browser_env": {
                                **execution_env({}),
                                **{
                                    key: str(directory / "temp")
                                    for key in ("TEMP", "TMP", "TMPDIR")
                                },
                            },
                            "downloads_path": str(directory / "downloads"),
                            "ownership_file": str(directory / "ownership.json"),
                            "allowed_origins": self.allowed_origins,
                            "timeout_ms": max(1, int((deadline - time.monotonic()) * 1000)),
                        }
                    ).encode()
                    + b"\n"
                )
                await process.stdin.drain()
                result = await self.response(session)
                session.version = result["version"]
                session.cdp_scope = result.get("cdp_cleanup")
                if session.cdp_scope is not None:
                    session.ownership = (
                        "borrowed"
                        if data["context_mode"] == "existing"
                        else "external_browser_owned_context"
                    )
                session.state = "ACTIVE"
                session.revision += 1
                session.quota_task = asyncio.create_task(self.monitor(session))
                self.emit(session, "inventory", "active")
                return {
                    "browser_id": session.id,
                    "pages": result["pages"],
                    "backend_version": session.version,
                    "handle": self.handle(session),
                    "ownership": session.ownership,
                    "profile": "existing_explicit"
                    if session.ownership == "borrowed"
                    else "isolated_ephemeral",
                    "sandbox": "external_unverified" if session.cdp_scope else True,
                }
            except BaseException:
                await self.close(session, state="FAILED")
                raise

    async def close(self, session: BrowserSession, *, state: str = "CLOSED") -> dict[str, Any]:
        if session.close_task is None:
            session.close_task = asyncio.create_task(self._close(session, state=state))
        while True:
            try:
                return await asyncio.shield(session.close_task)
            except asyncio.CancelledError:
                if session.close_task.cancelled():
                    session.cleanup_status = "unknown"
                    raise RACPError(
                        "EXECUTION_UNKNOWN",
                        "browser cleanup interrupted",
                        layer="provider",
                        execution_state="unknown",
                        cleanup_status="unknown",
                    ) from None
                # Provider deadline and the Agent deadline/cancel can arrive together.
                # Never publish a terminal operation before its owned cleanup finishes.
                continue

    async def _close(self, session: BrowserSession, *, state: str) -> dict[str, Any]:
        if session.close_result is not None:
            return session.close_result
        # Mark before yielding so concurrent close/lease/timeout cleanup kills once.
        session.state = state
        session.revision += 1
        for page in session.pages.values():
            page["revision"] += 1
        if session.directory is not None:
            try:
                record = json.loads(
                    (session.directory / "ownership.json").read_text(encoding="utf-8")
                )
                session.cdp_scope = record.get("remote_cdp", session.cdp_scope)
            except (OSError, ValueError):
                session.cleanup_status = "unknown"
        if session.cdp_scope and not await browser_cdp.cleanup(
            session.cdp_scope, time.monotonic() + 5
        ):
            session.cleanup_status = "unknown"
        if session.job is not None:
            import win32api
            import win32con
            import win32event
            import win32job

            # Accounting reaches zero before every process handle is signalled.
            # Retain kernel handles before kill so PID reuse cannot affect the wait.
            process_handles = []
            try:
                descendants = psutil.Process(session.process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            for child in descendants:
                try:
                    child_handle = win32api.OpenProcess(
                        win32con.SYNCHRONIZE | win32con.PROCESS_QUERY_INFORMATION, False, child.pid
                    )
                    if win32job.IsProcessInJob(child_handle, session.job):
                        process_handles.append(child_handle)
                    else:
                        child_handle.Close()
                except OSError:
                    pass  # Already exited or inaccessible sandbox process; Job still owns it.
            win32job.TerminateJobObject(session.job, 1)
            cleanup_deadline = time.monotonic() + 5
            try:
                while win32job.QueryInformationJobObject(
                    session.job, win32job.JobObjectBasicAccountingInformation
                )["ActiveProcesses"] or any(
                    win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT
                    for handle in process_handles
                ):
                    if time.monotonic() >= cleanup_deadline:
                        session.cleanup_status = "unknown"
                        break
                    await asyncio.sleep(0.01)
            finally:
                for child_handle in process_handles:
                    child_handle.Close()
                session.job.Close()
                session.job = None
        elif os.name != "nt":
            try:
                kill_group(session.process.pid)
            except ProcessLookupError:
                pass
        if session.process.returncode is None:
            try:
                session.process.kill()
            except ProcessLookupError:
                pass
        await session.process.wait()
        if session.receiver is not None:
            session.receiver.cancel()
            await asyncio.gather(session.receiver, return_exceptions=True)
        if session.quota_task is not None:
            session.quota_task.cancel()
        if session.directory is not None and session.cleanup_status == "complete":
            try:
                await asyncio.to_thread(self.workspace.remove, session.directory)
                session.disk_bytes = 0
            except (OSError, RACPError):
                session.cleanup_status = "unknown"
        session.close_result = {
            "browser_id": session.id,
            "state": state,
            "cleanup_status": session.cleanup_status,
            "handle": self.handle(session),
            "resource_disposition": "external_browser_preserved"
            if session.cdp_scope
            else "owned_browser_terminated",
        }
        self.emit(session, "inventory", state.lower())
        return session.close_result

    async def recover_remote(self) -> None:
        for path in list(self.workspace.root.iterdir())[:24]:
            try:
                self.workspace.checked(path)
                marker = path / "ownership.json"
                record = json.loads(marker.read_text(encoding="utf-8"))
                scope = record.get("remote_cdp")
                if scope is not None and not isinstance(scope, dict):
                    continue
                if (
                    not scope
                    or self.workspace.alive(record["owner_pid"], record["owner_birth"])
                    or self.workspace.alive(record["worker_pid"], record["worker_birth"])
                ):
                    continue
                if await browser_cdp.cleanup(scope, time.monotonic() + 5):
                    self.workspace.remove(path)
            except (OSError, ValueError, KeyError, TypeError, RACPError):
                continue

    async def monitor(self, session: BrowserSession) -> None:
        assert session.directory is not None
        while session.state == "ACTIVE":
            await asyncio.sleep(0.05)
            try:
                session.disk_bytes = await asyncio.to_thread(
                    self.workspace.usage, session.directory
                )
                download_bytes = sum(
                    p.stat().st_size
                    for p in (session.directory / "downloads").iterdir()
                    if p.is_file()
                )
                exhausted = (
                    session.download_limit is not None
                    and download_bytes > session.download_limit
                    or sum(s.disk_bytes for s in self.sessions.values())
                    + sum(p.stat().st_size for p in self.spool.iterdir() if p.is_file())
                    > self.disk_limit_bytes
                )
            except FileNotFoundError:
                if session.state != "ACTIVE":
                    return
                continue
            except (OSError, RACPError):
                exhausted = True
            if exhausted:
                session.failure_code = "RESOURCE_EXHAUSTED"
                await self.close(session, state="FAILED")
                return

    async def cleanup(self, *, expired_only: bool = False) -> None:
        for session in list(self.sessions.values()):
            if expired_only and time.monotonic() < session.expires:
                continue
            await self.close(session, state="EXPIRED")
            if expired_only:
                self.sessions.pop(session.id, None)

    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        deadline = time.monotonic() + context.timeout_ms / 1000
        session: BrowserSession | None = None
        dispatched = False
        try:
            async with asyncio.timeout_at(deadline):
                if operation == "browser.cdp_targets":
                    if not self.cdp_enabled:
                        raise RACPError(
                            "PERMISSION_DENIED", "CDP discovery disabled locally", layer="provider"
                        )
                    result = await browser_cdp.targets(payload["endpoint_url"])
                elif operation in {"browser.open", "browser.attach"}:
                    result = await self.open(payload, context, deadline)
                else:
                    session = self.identity(payload, context)
                    if operation == "browser.close":
                        result = await self.close(session)
                    else:
                        async with session.lock:
                            if session.state == "CREATING":
                                raise RACPError(
                                    "RESOURCE_BUSY",
                                    "browser startup is still in progress",
                                    layer="provider",
                                )
                            if time.monotonic() >= session.expires:
                                await self.close(session, state="EXPIRED")
                            if session.state != "ACTIVE":
                                raise RACPError(
                                    "HANDLE_EXPIRED", "browser is closed/expired", layer="provider"
                                )
                            if operation == "browser.keepalive":
                                session.touch()
                                result = {"browser_id": session.id, "handle": self.handle(session)}
                            else:
                                data = dict(payload)
                                if operation == "browser.screenshot":
                                    data["_output_path"] = str(
                                        self.spool / (context.operation_id + ".png")
                                    )
                                if operation == "browser.download":
                                    data["_output_path"] = str(
                                        self.spool / (context.operation_id + ".download")
                                    )
                                    session.download_limit = int(payload["max_bytes"])
                                if operation == "browser.upload":
                                    assert session.directory is not None
                                    data["_upload_directory"] = str(
                                        session.directory / "uploads" / context.operation_id
                                    )
                                assert session.process.stdin is not None
                                session.process.stdin.write(
                                    json.dumps(
                                        {
                                            "action": operation.split(".")[1],
                                            "payload": data,
                                            "timeout": max(
                                                1, int((deadline - time.monotonic()) * 1000)
                                            ),
                                        }
                                    ).encode()
                                    + b"\n"
                                )
                                dispatched = True
                                await session.process.stdin.drain()
                                result = await self.response(session)
                                session.download_limit = None
                                if operation not in {
                                    "browser.pages",
                                    "browser.frames",
                                    "browser.snapshot",
                                    "browser.screenshot",
                                }:
                                    session.touch()
                                result.update(
                                    browser_id=session.id,
                                    handle=self.handle(
                                        session, payload.get("page_id") or result.get("page_id")
                                    ),
                                )
                return {"state": "SUCCEEDED", "result": result}
        except (TimeoutError, asyncio.CancelledError, RACPError, OSError) as exc:
            code = (
                exc.error.code
                if isinstance(exc, RACPError)
                else "TIMEOUT"
                if isinstance(exc, TimeoutError)
                else "CANCELLED"
                if isinstance(exc, asyncio.CancelledError)
                else "EXECUTION_UNKNOWN"
            )
            if (
                session is not None
                and dispatched
                and (
                    code in {"TIMEOUT", "CANCELLED", "EXECUTION_UNKNOWN", "BROWSER_ERROR"}
                    or code == "RESOURCE_EXHAUSTED"
                    and operation == "browser.download"
                )
            ):
                cleanup = asyncio.create_task(self.close(session, state="FAILED"))
                cleanup_interrupted = False
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        break
                    except asyncio.CancelledError:
                        cleanup_interrupted = True
                if cleanup_interrupted:
                    raise asyncio.CancelledError from exc
            if operation == "browser.screenshot":
                (self.spool / (context.operation_id + ".png")).unlink(missing_ok=True)
            if operation == "browser.download":
                (self.spool / (context.operation_id + ".download")).unlink(missing_ok=True)
                if session is not None:
                    session.download_limit = None
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, RACPError):
                if session and session.close_result:
                    raise RACPError(
                        exc.error.code,
                        exc.error.message,
                        layer=exc.error.layer,
                        execution_state=exc.error.execution_state,
                        **{
                            **exc.error.details,
                            "cleanup_status": session.cleanup_status,
                            "browser_closed": True,
                        },
                    ) from exc
                raise
            raise RACPError(
                code,
                "browser operation interrupted; inspect before replay",
                layer="provider",
                execution_state="unknown" if dispatched else "not_started",
                cleanup_status=session.cleanup_status
                if session and session.close_result
                else "not_required",
            ) from exc
