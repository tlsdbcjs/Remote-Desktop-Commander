"""SCM host entry point. Installation/account provisioning belong to the release installer."""

import argparse
import asyncio
import os
import threading
from pathlib import Path
from typing import Any

from racp_sdk.security import SecretStore, digest

from racp_agent.broker.identity import process_identity
from racp_agent.instance_lock import InstanceLock
from racp_agent.plugins.config import load_plugin_config
from racp_agent.runtime import Agent
from racp_agent.service_config import ServiceConfig, load_service_config


class ServiceRunner:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.loop: asyncio.AbstractEventLoop | None = None
        self.agent: Agent | None = None
        self.task: asyncio.Task[None] | None = None
        self.stop_requested = threading.Event()

    def stop(self) -> None:
        self.stop_requested.set()

        def interrupt() -> None:
            if self.agent is not None:
                self.agent.stopping.set()
            if self.task is not None and not self.task.cancelling():
                self.task.cancel()

        if self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(interrupt)
            except RuntimeError:
                pass  # The event loop has already completed service shutdown.

    async def run(self) -> None:
        self.config.check_identity(process_identity(os.getpid()))
        credentials = SecretStore(self.config.credentials).load()
        device_lock = self.config.credentials.parent / (
            "agent-" + digest(credentials["device_id"]) + ".lock"
        )
        with InstanceLock(device_lock), InstanceLock(self.config.data_dir / "agent.lock"):
            await self.run_locked(credentials)

    async def run_locked(self, credentials: dict[str, str]) -> None:
        self.loop = asyncio.get_running_loop()
        config = self.config
        self.agent = Agent(
            credentials["gateway"],
            credentials["credential"],
            credentials["device_id"],
            config.workspace,
            config.data_dir,
            profile=config.profile,
            allowed_workspaces=tuple(config.allowed_workspaces),
            browser_allowed_origins=tuple(config.browser_allowed_origins),
            ca_file=config.ca_file
            or (Path(credentials["ca_file"]) if credentials.get("ca_file") else None),
            browser_cdp=config.browser_cdp,
            desktop_login_users=tuple(config.desktop_login_users),
            service_sid=config.service_sid,
            plugins=load_plugin_config(config.plugin_config),
        )
        self.task = asyncio.create_task(self.agent.run())
        if self.stop_requested.is_set():
            self.stop()
        try:
            await self.task
        except asyncio.CancelledError:
            if not self.stop_requested.is_set():
                raise
        finally:
            # Also closes the journal when STOP arrived before the Agent's first turn.
            self.agent.journal.close()
            self.loop = None


def service_class(config: ServiceConfig) -> Any:
    import win32service
    import win32serviceutil

    class AgentService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = config.service_name
        _svc_display_name_ = "RACP Agent"

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            self.runner = ServiceRunner(config)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=10000)
            self.runner.stop()

        def SvcShutdown(self) -> None:
            self.SvcStop()

        def SvcDoRun(self) -> None:
            asyncio.run(self.runner.run())

    return AgentService


def main() -> None:
    parser = argparse.ArgumentParser(description="RACP nonadministrator SCM Agent host")
    parser.add_argument("--config-file", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise OSError("SCM Agent host requires Windows")
    import servicemanager

    configured = service_class(load_service_config(args.config_file))
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(configured)
    servicemanager.StartServiceCtrlDispatcher()


if __name__ == "__main__":
    main()
