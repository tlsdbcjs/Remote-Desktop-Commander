"""Temporary current-user interactive launch when an outer host job prevents breakaway."""

import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError

from racp_agent.broker.guard_client import guard_request, pin_guard
from racp_agent.broker.guard_config import GuardConfig
from racp_agent.broker.identity import process_identity
from racp_agent.broker.peer_process import PeerProcess
from racp_agent.broker.pipe import NativeError


class StartupCleanupUnverified(RACPError):
    def __init__(self) -> None:
        super().__init__(
            "EXECUTION_UNKNOWN",
            "input guardian startup termination unverified; private recovery files retained",
            layer="broker",
            execution_state="unknown",
            cleanup_status="unverified",
        )


def task_missing(error: Any) -> bool:
    code = error.excepinfo[5] if error.excepinfo else error.hresult
    return bool(code & 0xFFFFFFFF in {0x80070002, 0x8004130F})


def stop_registered(task: Any) -> None:
    import pythoncom

    try:
        task.Stop(0)
        deadline = time.monotonic() + 5
        while True:
            # These tasks are always created/run as the current logon user. Do not
            # infer another security context's termination from filtered instances.
            instances = task.GetInstances(0)
            count = instances.Count
            instances = None
            if count == 0 and task.State in {1, 3}:  # DISABLED / READY, never QUEUED.
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("input guardian task instances did not stop")
            time.sleep(0.01)
    except pythoncom.com_error as exc:
        if not task_missing(exc):
            raise


def stop_startup_task(name: str) -> None:
    """Only before attachment: no input has been armed under this task's marker."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitializeEx(0)
    service = folder = task = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        folder = service.GetFolder("\\")
        try:
            task = folder.GetTask(name)
        except pythoncom.com_error as exc:
            if task_missing(exc):
                return
            raise
        stop_registered(task)
    finally:
        task = folder = service = None
        pythoncom.CoUninitialize()


def remove_task(name: str) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitializeEx(0)
    service = folder = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        folder = service.GetFolder("\\")
        try:
            folder.DeleteTask(name, 0)
        except pythoncom.com_error as exc:
            if not task_missing(exc):
                raise
    finally:
        folder = service = None
        pythoncom.CoUninitialize()


def launch_task(config: GuardConfig, file: Path) -> str:
    import pythoncom
    import win32com.client
    import win32security

    actor = process_identity(__import__("os").getpid())
    if actor.sid != config.user_sid or actor.session != config.session_id:
        raise PermissionError("temporary input guardian task must run as its current logon user")
    name = "RACP-InputGuard-" + config.pair_id
    account, domain, _ = win32security.LookupAccountSid(
        None, win32security.ConvertStringSidToSid(actor.sid)
    )
    username = domain + "\\" + account
    pythoncom.CoInitializeEx(0)
    service = folder = definition = task = running = action = principal = settings = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        folder = service.GetFolder("\\")
        definition = service.NewTask(0)
        definition.RegistrationInfo.Description = "Temporary RACP release-only input guardian"
        principal = definition.Principal
        principal.UserId, principal.LogonType, principal.RunLevel = username, 3, 0
        settings = definition.Settings
        settings.Hidden, settings.AllowDemandStart = True, True
        settings.DisallowStartIfOnBatteries, settings.StopIfGoingOnBatteries = False, False
        settings.ExecutionTimeLimit = "PT0S"
        action = definition.Actions.Create(0)
        executable = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
        if not executable.is_file():
            raise FileNotFoundError("windowless installed Python runtime unavailable")
        action.Path = str(executable)
        action.Arguments = subprocess.list2cmdline(
            [
                "-I",
                str(Path(__file__).with_name("guard_bootstrap.py").resolve()),
                "--config-file",
                str(file),
                "--site",
                sysconfig.get_path("purelib"),
            ]
        )
        # CREATE only, never overwrite an existing task; no trigger/password/elevation.
        task = folder.RegisterTaskDefinition(
            name,
            definition,
            2,
            None,
            None,
            3,
            f"D:P(A;;GA;;;SY)(A;;GA;;;{actor.sid})(A;;GA;;;{config.controller_principal})",
        )
        running = task.RunEx(None, 4, actor.session, username)  # TASK_RUN_USE_SESSION_ID
        assert running is not None
        return name
    except BaseException as exc:
        if task is not None and folder is not None:
            try:
                stop_registered(task)
                try:
                    folder.DeleteTask(name, 0)
                except pythoncom.com_error as missing:
                    if not task_missing(missing):
                        raise
            except Exception as cleanup:
                raise StartupCleanupUnverified() from cleanup
        if isinstance(exc, pythoncom.com_error):
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "current-user input guardian task unavailable",
                layer="broker",
            ) from exc
        raise
    finally:
        running = action = principal = settings = task = definition = folder = service = None
        pythoncom.CoUninitialize()


class ScheduledGuard:
    def __init__(self, name: str, file: Path) -> None:
        self.name, self.file = name, file

    def cleanup(self) -> None:
        try:
            remove_task(self.name)
        finally:
            # Only the exact file/private directory created for this task are removed.
            self.file.unlink(missing_ok=True)
            try:
                self.file.parent.rmdir()
            except FileNotFoundError:
                pass


def cleanup_guard_config(config: GuardConfig) -> None:
    from racp_agent.providers.paths import is_link

    if config.launch_backend != "task" or config.cleanup_directory is None:
        return
    root = Path(config.cleanup_directory)
    if not root.is_absolute() or not root.name.startswith("racp-input-guard-"):
        raise PermissionError("input guardian cleanup root differs from bootstrap")
    file = root / (config.pair_id + ".json")
    if root.exists():
        if is_link(root.lstat()) or file.exists() and is_link(file.lstat()):
            raise PermissionError("input guardian cleanup cannot follow links")
        if file.exists():
            stored = GuardConfig.load(file)
            if (
                stored.pair_id != config.pair_id
                or stored.secret != config.secret
                or stored.cleanup_directory != config.cleanup_directory
            ):
                raise PermissionError("input guardian cleanup ownership differs")
    ScheduledGuard("RACP-InputGuard-" + config.pair_id, file).cleanup()


def start_scheduled(config: GuardConfig) -> tuple[GuardConfig, PeerProcess, ScheduledGuard]:
    directory = Path(tempfile.mkdtemp(prefix="racp-input-guard-"))
    config = config.model_copy(
        update={"launch_backend": "task", "cleanup_directory": str(directory.resolve())}
    )
    file = config.save(directory)
    name = None
    try:
        name = launch_task(config, file)
        deadline = time.monotonic() + 5
        while True:
            try:
                state = guard_request(config, "guard.status")
                ready = config.model_copy(
                    update={
                        "guardian_pid": state["guardian_pid"],
                        "guardian_created": state["guardian_created"],
                    }
                )
                peer = pin_guard(ready)
                if not state["healthy"] or not state["outside_broker_job"]:
                    peer.close()
                    raise PermissionError("scheduled input guardian not independent")
                return ready, peer, ScheduledGuard(name, file)
            except PermissionError:
                raise  # Identity/independence rejection is not transient readiness.
            except (NativeError, OSError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("scheduled input guardian readiness unavailable") from None
                time.sleep(0.02)
    except StartupCleanupUnverified:
        raise  # Preserve the only recovery configuration for a still-running task.
    except BaseException:
        if name is not None:
            try:
                stop_startup_task(name)
                remove_task(name)
            except Exception as cleanup:
                raise StartupCleanupUnverified() from cleanup
        file.unlink(missing_ok=True)
        directory.rmdir()
        raise
