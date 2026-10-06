"""Test-only guarded native hold on the explicitly observed foreground fixture window."""

import argparse
import time
from pathlib import Path
from typing import Any

from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.core import BrokerCore
from racp_agent.broker.identity import process_identity
from racp_agent.broker.pipe import PipeServer
from racp_agent.broker.windows import WindowsDesktop


class GuardedHold(WindowsDesktop):
    def action(self, operation: str, payload: dict[str, Any], guard: Any) -> dict[str, Any]:
        if operation == "desktop.type" and payload["text"] == "fixture_hold_for_kill":
            guard()
            if any(
                self.user.GetAsyncKeyState(code) & 0x8000 for code in (1, 2, 4, 16, 17, 18, 91, 92)
            ):
                raise PermissionError("fixture refuses to release pre-existing user modifiers")
            self.pressed.extend([self.key(vk=0xA2, flags=2), self.mouse(4)])
            self.send([self.key(vk=0xA2), self.mouse(2)])
            # No input payload can select a shell/process or this fixture outside tests.
            time.sleep(90)
            return {"fixture": True}
        return super().action(operation, payload, guard)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairing", type=Path, required=True)
    config = BrokerConfig.load(parser.parse_args().pairing)
    backend = GuardedHold(config.session_id, require_guard=True)
    core = BrokerCore(config.session_id, backend)
    server = PipeServer(config)
    try:
        while True:
            config.require_agent(process_identity(config.agent_pid))
            core.tick()
            server.accept(core.handle)
    finally:
        try:
            core.abort()
        finally:
            try:
                server.close()
            finally:
                backend.close()


if __name__ == "__main__":
    main()
