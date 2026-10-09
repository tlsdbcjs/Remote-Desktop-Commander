"""Gateway SCM lifecycle stays bounded and independent from an interactive user session."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

from racp_gateway.config import GatewayConfig
from racp_gateway.windows_service import ScmLifecycle, _serve_gateway


def service_config(root: Path) -> GatewayConfig:
    return GatewayConfig(
        instance_id="gateway_service_test",
        mode="service",
        bind_address="127.0.0.1",
        port=18765,
        public_origin="http://127.0.0.1:18765",
        state_root=str(root.resolve()),
    )


def test_scm_start_stop_reports_state(tmp_path: Path) -> None:
    states: list[tuple[str, int]] = []
    running = threading.Event()

    def runner(config: GatewayConfig, stop_event: threading.Event) -> int:
        assert config.mode == "service"
        running.set()
        assert stop_event.wait(2)
        return 0

    lifecycle = ScmLifecycle(service_config(tmp_path), states.append, runner=runner)  # type: ignore[arg-type]
    # list.append accepts one tuple, while the lifecycle reporter has two positional values.
    lifecycle.report = lambda state, wait: states.append((state, wait))
    thread = threading.Thread(target=lifecycle.run)
    thread.start()
    assert running.wait(2)
    lifecycle.stop()
    thread.join(2)

    assert not thread.is_alive()
    assert [state for state, _ in states] == [
        "START_PENDING",
        "RUNNING",
        "STOP_PENDING",
        "STOPPED",
    ]
    assert states[0][1] > 0 and states[2][1] > 0


def test_service_uvicorn_does_not_require_console_streams(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class Config:
        def __init__(self, application: object, **options: object) -> None:
            captured.update(options)

    class Server:
        should_exit = False

        def __init__(self, config: object) -> None:
            pass

        async def serve(self, sockets: object = None) -> None:
            self.should_exit = True

    stop = threading.Event()
    with patch("racp_gateway.windows_service.uvicorn.Config", Config), patch(
        "racp_gateway.windows_service.uvicorn.Server", Server
    ):
        asyncio.run(_serve_gateway(service_config(tmp_path), stop))

    assert captured["log_config"] is None
    assert captured["access_log"] is False
