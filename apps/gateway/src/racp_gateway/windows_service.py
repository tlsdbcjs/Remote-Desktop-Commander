"""Windows SCM adapter for the persistent RACP Gateway service."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import socket
import threading
from collections.abc import Callable
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any

import uvicorn
from racp_sdk.connection_file import validate_ca_pem
from racp_sdk.security import SecretStore, digest, token

from racp_gateway.app import create_app
from racp_gateway.config import (
    GatewayConfig,
    load_gateway_config,
    resolve_gateway_paths,
    validate_gateway_config,
)
from racp_gateway.local_admin import LocalAdminBroker, LocalAdminPipeServer
from racp_gateway.loop import create_loop
from racp_gateway.network import GatewayNetwork
from racp_gateway.oauth import load_oauth_config
from racp_gateway.store import GatewayStore

SERVICE_NAME = "RACP Gateway"


class LocalAdminWorker:
    def __init__(self, config: GatewayConfig, stop_event: threading.Event) -> None:
        self.config = config
        self.stop_event = stop_event
        self.started = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(
            target=self._run,
            name="racp-gateway-local-admin",
            daemon=True,
        )

    def _run(self) -> None:
        store: GatewayStore | None = None
        server: LocalAdminPipeServer | None = None
        try:
            paths = resolve_gateway_paths(self.config)
            store = GatewayStore(paths.database)
            store.initialize()
            server = LocalAdminPipeServer(LocalAdminBroker(store))
            self.started.set()
            while not self.stop_event.is_set():
                server.accept(250)
        except BaseException as exc:
            self.error = exc
            self.started.set()
        finally:
            if server is not None:
                server.close()
            if store is not None:
                store.close()

    def start(self) -> None:
        self.thread.start()
        if not self.started.wait(5):
            raise TimeoutError("local admin pipe did not initialize")
        if self.error is not None:
            raise RuntimeError("local admin pipe failed to initialize") from self.error

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)


class GatewayInstanceLock:
    """Cross-process single-instance lock scoped to one configured Gateway state root."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: Any | None = None

    def __enter__(self) -> GatewayInstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl_module: Any = importlib.import_module("fcntl")
                fcntl_module.flock(
                    self.stream.fileno(),
                    fcntl_module.LOCK_EX | fcntl_module.LOCK_NB,
                )
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError("Gateway state is already owned by another process") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl_module = importlib.import_module("fcntl")
                fcntl_module.flock(self.stream.fileno(), fcntl_module.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def ensure_service_owner(config: GatewayConfig) -> str:
    """Create/load owner material under the running service identity only."""
    if config.mode != "service":
        raise ValueError("service owner material is only valid in service mode")
    paths = resolve_gateway_paths(config)
    secret_path = paths.secrets / "owner.bin"
    store = GatewayStore(paths.database)
    try:
        store.initialize()
        owner = store.db.execute("SELECT digest FROM owner WHERE id='owner_local'").fetchone()
        if secret_path.exists():
            saved = SecretStore(secret_path).load()
            owner_token = saved.get("token")
            if not owner_token:
                raise PermissionError("service owner credential is incomplete")
            if owner is None:
                store.initialize(digest(owner_token))
            elif str(owner["digest"]) != digest(owner_token):
                raise PermissionError("service owner credential does not match Gateway identity")
            return owner_token
        if owner is not None:
            raise PermissionError(
                "Gateway owner exists without a service-owned credential; "
                "explicit migration is required"
            )
        owner_token = token()
        SecretStore(secret_path).save({"token": owner_token}, overwrite=False)
        try:
            store.initialize(digest(owner_token))
        except BaseException:
            secret_path.unlink(missing_ok=True)
            raise
        return owner_token
    finally:
        store.close()


def _client_ca(config: GatewayConfig) -> str | None:
    if config.tls.client_ca_file is None:
        return None
    path = Path(config.tls.client_ca_file)
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Client CA exceeds its bound")
    return validate_ca_pem(raw.decode("utf-8"))


def _application(config: GatewayConfig) -> Any:
    paths = resolve_gateway_paths(config)
    oauth = (
        load_oauth_config(Path(config.auth.oauth_config_file))
        if config.auth.oauth_config_file
        else None
    )
    return create_app(
        paths.state_root,
        public_origin=config.public_origin,
        oauth_config=oauth,
        client_ca_pem=_client_ca(config),
        local_mcp_port=config.auth.local_mcp_port,
        gateway_config_path=paths.config_file,
    )


async def _serve_gateway(config: GatewayConfig, stop_event: threading.Event) -> int:
    certificate = Path(config.tls.certificate_file) if config.tls.certificate_file else None
    private_key = Path(config.tls.private_key_file) if config.tls.private_key_file else None
    network = GatewayNetwork(
        config.bind_address,
        config.port,
        config.public_origin,
        certificate,
        private_key,
    )
    network.validate()
    application = _application(config)
    options = dict(
        network.uvicorn_options(),
        ws_max_size=1024 * 1024,
        ws_per_message_deflate=False,
        workers=1,
        loop="none",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(uvicorn.Config(application, **options))  # type: ignore[arg-type]
    # pywin32 invokes the service body on its service thread, where Python's
    # signal.signal() is not permitted. SCM controls already drive stop_event.
    server.capture_signals = nullcontext  # type: ignore[method-assign,assignment]

    async def watch_stop() -> None:
        while not stop_event.is_set() and not server.should_exit:  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        if stop_event.is_set():
            server.should_exit = True

    watcher = asyncio.create_task(watch_stop())
    with ExitStack() as resources:
        listeners: list[socket.socket] | None = None
        if config.auth.local_mcp_port is not None:
            listeners = []
            for host, port in [
                (config.bind_address, config.port),
                ("127.0.0.1", config.auth.local_mcp_port),
            ]:
                family = socket.AF_INET6 if ":" in host else socket.AF_INET
                listener = resources.enter_context(socket.socket(family, socket.SOCK_STREAM))
                listener.bind((host, port))
                listener.listen(128)
                listeners.append(listener)
        try:
            await server.serve(sockets=listeners)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    return 0


def run_gateway(config: GatewayConfig, stop_event: threading.Event) -> int:
    """Run exactly one configured Gateway instance until SCM requests stop."""
    check = validate_gateway_config(config)
    if not check.valid:
        raise ValueError("; ".join(check.errors))
    paths = resolve_gateway_paths(config)
    with GatewayInstanceLock(paths.state_root / "gateway.lock"):
        admin_worker: LocalAdminWorker | None = None
        if config.mode == "service":
            ensure_service_owner(config)
            admin_worker = LocalAdminWorker(config, stop_event)
            admin_worker.start()
        loop = create_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(_serve_gateway(config, stop_event))
        finally:
            stop_event.set()
            if admin_worker is not None:
                admin_worker.close()
            asyncio.set_event_loop(None)
            loop.close()


class ScmLifecycle:
    """Testable SCM state machine; the pywin32 class below only adapts callbacks."""

    def __init__(
        self,
        config: GatewayConfig,
        report: Callable[[str, int], None],
        *,
        runner: Callable[[GatewayConfig, threading.Event], int] = run_gateway,
    ) -> None:
        self.config = config
        self.report = report
        self.runner = runner
        self.stop_event = threading.Event()
        self.stop_pending = False

    def stop(self) -> None:
        if not self.stop_pending:
            self.stop_pending = True
            self.report("STOP_PENDING", 10000)
        self.stop_event.set()

    def run(self) -> int:
        self.report("START_PENDING", 15000)
        self.report("RUNNING", 0)
        try:
            return self.runner(self.config, self.stop_event)
        finally:
            if not self.stop_pending:
                self.report("STOP_PENDING", 10000)
            self.report("STOPPED", 0)


if os.name == "nt":
    import win32service as _win32service
    import win32serviceutil as _win32serviceutil

    class GatewayService(_win32serviceutil.ServiceFramework):  # type: ignore[misc]
        """Static pywin32 entry point loaded by pythonservice.exe."""

        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_NAME

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            config_file = _win32serviceutil.GetServiceCustomOption(args[0], "ConfigFile")
            if not config_file:
                raise ValueError("Gateway service ConfigFile option is missing")
            self.lifecycle = ScmLifecycle(load_gateway_config(Path(str(config_file))), self._report)

        def _report(self, state: str, wait_hint: int) -> None:
            states = {
                "START_PENDING": _win32service.SERVICE_START_PENDING,
                "RUNNING": _win32service.SERVICE_RUNNING,
                "STOP_PENDING": _win32service.SERVICE_STOP_PENDING,
                "STOPPED": _win32service.SERVICE_STOPPED,
            }
            self.ReportServiceStatus(states[state], waitHint=wait_hint)

        def SvcStop(self) -> None:
            self.lifecycle.stop()

        def SvcShutdown(self) -> None:
            self.lifecycle.stop()

        def SvcRun(self) -> None:
            # pythonservice.exe normally reports RUNNING before invoking SvcDoRun.
            # This service owns a richer START_PENDING -> RUNNING lifecycle, so
            # bypass ServiceFramework.SvcRun and let ScmLifecycle be authoritative.
            self.lifecycle.run()


def service_class(config: GatewayConfig) -> Any:
    import win32service
    import win32serviceutil

    states = {
        "START_PENDING": win32service.SERVICE_START_PENDING,
        "RUNNING": win32service.SERVICE_RUNNING,
        "STOP_PENDING": win32service.SERVICE_STOP_PENDING,
        "STOPPED": win32service.SERVICE_STOPPED,
    }

    class GatewayService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_NAME

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            self.lifecycle = ScmLifecycle(config, self._report)

        def _report(self, state: str, wait_hint: int) -> None:
            self.ReportServiceStatus(states[state], waitHint=wait_hint)

        def SvcStop(self) -> None:
            self.lifecycle.stop()

        def SvcShutdown(self) -> None:
            self.lifecycle.stop()

        def SvcDoRun(self) -> None:
            self.lifecycle.run()

    return GatewayService


def main() -> None:
    parser = argparse.ArgumentParser(description="RACP Gateway Windows SCM host")
    parser.add_argument("--config-file", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise OSError("Gateway SCM host requires Windows")
    import servicemanager

    configured = service_class(load_gateway_config(args.config_file))
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(configured)
    servicemanager.StartServiceCtrlDispatcher()


if __name__ == "__main__":
    main()
