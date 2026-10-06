"""Isolated transport fixture; generates pixels and never captures or inputs the user's desktop."""

import argparse
import os
import time
from pathlib import Path
from typing import Any

from racp_agent.broker.capture import png, preview
from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.core import BrokerCore
from racp_agent.broker.identity import process_identity
from racp_agent.broker.pipe import PipeServer


class SyntheticDesktop:
    def __init__(self, session_id: int) -> None:
        self.session_id = session_id
        self.screenshot_count = 0
        self.data = os.urandom(512 * 512 * 4)

    def status(self) -> dict[str, Any]:
        return {
            "available": True,
            "broker_pid": os.getpid(),
            "broker_created": process_identity(os.getpid()).created,
            "session_id": self.session_id,
            "user_sid": process_identity(os.getpid()).sid,
            "fixture": "synthetic_pixels_no_gui_input",
        }

    def layout(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "layout_revision": "layout_synthetic",
            "virtual_origin": {"x": -1920, "y": -300},
            "width": 512,
            "height": 512,
            "monitors": [
                {
                    "monitor_id": "monitor_synthetic",
                    "origin": {"x": -1920, "y": -300},
                    "width": 512,
                    "height": 512,
                    "scale_x": 1.5,
                    "scale_y": 1.5,
                }
            ],
        }

    def foreground(self) -> str:
        return "window_synthetic"

    def input_tick(self) -> int:
        return 1

    def foreground_tick(self) -> int:
        return 0

    def acquire_lease(self) -> None:
        pass

    def release_lease(self) -> None:
        pass

    def windows(self, limit: int) -> list[dict[str, Any]]:
        return [{"window_id": "window_synthetic", "bounds": [-1920, -300, -1408, 212]}][:limit]

    def validate_window(self, window: dict[str, Any]) -> bool:
        return window["window_id"] == "window_synthetic"

    def release_inputs(self) -> None:
        pass

    def inspect(self, payload: dict[str, Any], observation: str) -> dict[str, Any]:
        return {}

    def clear_observations(self) -> None:
        pass

    def attach_guard(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"attached": True, "fixture": "no_actual_input"}

    def guard_info(self) -> dict[str, Any]:
        return {"guardian": None}

    def action(self, operation: str, payload: dict[str, Any], guard: Any) -> dict[str, Any]:
        guard()
        if payload.get("text") == "fixture_hang":
            time.sleep(90)
        return {"dispatched": True, "fixture": "no_actual_gui_input"}

    def capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.screenshot_count += 1
        result: dict[str, Any] = {
            "_png": png(512, 512, self.data),
            "width": 512,
            "height": 512,
            "crop_origin": {"x": -1920, "y": -300},
            "format": "png",
            "capture_scope": "synthetic_fixture",
            "count": self.screenshot_count,
        }
        if payload["preview"]:
            encoded, metadata = preview(512, 512, self.data)
            result["_preview_png"] = encoded
            result["preview"] = {**metadata, "crop_origin": {"x": -1920, "y": -300}}
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairing", type=Path, required=True)
    args = parser.parse_args()
    config = BrokerConfig.load(args.pairing)
    server = PipeServer(config)
    core = BrokerCore(config.session_id, SyntheticDesktop(config.session_id))
    try:
        while True:
            config.require_agent(process_identity(config.agent_pid))
            core.tick()
            server.accept(core.handle)
    finally:
        core.abort()
        server.close()


if __name__ == "__main__":
    main()
