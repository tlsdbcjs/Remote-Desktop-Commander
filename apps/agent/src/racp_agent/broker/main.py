"""Interactive-user entry point. Pairing is supplied by the local Agent, never by HTTP."""

import argparse
import os
from pathlib import Path

from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.core import BrokerCore
from racp_agent.broker.guard_config import GuardConfig
from racp_agent.broker.identity import process_identity
from racp_agent.broker.pipe import PipeServer
from racp_agent.broker.session_state import query, same_logon
from racp_agent.broker.windows import WindowsDesktop


def run_config(config: BrokerConfig, guardian: GuardConfig | None = None) -> None:
    if os.name != "nt":
        raise OSError("Session Broker requires Windows")
    config.require_agent(process_identity(config.agent_pid))
    config.require_broker(process_identity(os.getpid()))
    backend = WindowsDesktop(config.session_id, require_guard=True)
    core = BrokerCore(config.session_id, backend)
    server = None
    try:
        if guardian is not None:
            backend.attach_guard(guardian.model_dump())
        server = PipeServer(config)
        while True:
            # Process identity includes birth time: exit/PID reuse terminates this pairing.
            config.require_agent(process_identity(config.agent_pid))
            if not same_logon(query(config.session_id), config.user_sid):
                break
            core.tick()
            server.accept(core.handle)
    finally:
        try:
            core.abort()
        finally:
            try:
                if server is not None:
                    server.close()
            finally:
                backend.close()


def run(path: Path) -> None:
    run_config(BrokerConfig.load(path))


def main() -> None:
    parser = argparse.ArgumentParser(description="RACP local interactive-session Broker")
    parser.add_argument("--pairing", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.pairing)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
