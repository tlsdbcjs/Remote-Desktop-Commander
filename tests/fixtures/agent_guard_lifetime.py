"""Actual Agent-process lifetime fixture; input is confined to an explicit owned test window."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from racp_agent.broker.guard_client import guard_request
from racp_agent.broker.identity import process_identity
from racp_agent.broker.supervisor import BrokerSupervisor


async def run(root: Path, window_pid: int | None) -> None:
    identity = process_identity(os.getpid())
    broker = BrokerSupervisor(root, identity.session, "dev_guard_crash")
    if window_pid is not None:
        script = Path(__file__).with_name("native_guarded_broker.py")
        broker._command = lambda path: [sys.executable, "-I", str(script), "--pairing", str(path)]  # type: ignore[method-assign]
    work = None
    try:
        await broker.start()
        if broker.guardian is None:
            raise RuntimeError("fixture input guardian unavailable")
        guardian = broker.guardian
        if window_pid is not None:
            context = {
                "owner_id": "owner_native",
                "device_id": "dev_guard_crash",
                "operation_id": "op_guard_crash",
                "timeout_ms": 10000,
            }
            lease = await broker.call(
                "desktop.lease_acquire", {"session_id": identity.session}, context, 3
            )
            observed = await broker.call(
                "desktop.windows", {"session_id": identity.session}, context, 3
            )
            target = next(
                w
                for w in observed["windows"]
                if w["pid"] == window_pid and w["title"] == "RACP temporary acceptance window"
            )
            common = {
                "session_id": identity.session,
                "lease_id": lease["lease_id"],
                "observation_id": observed["observation_id"],
                "layout_revision": observed["layout_revision"],
                "expected_window_id": target["window_id"],
            }
            left, top, right, bottom = target["bounds"]
            await broker.call(
                "desktop.move",
                {**common, "x": (left + right) // 2, "y": top + (bottom - top) * 3 // 4},
                context,
                3,
            )
            observed = await broker.call(
                "desktop.windows", {"session_id": identity.session}, context, 3
            )
            common["observation_id"] = observed["observation_id"]
            work = asyncio.create_task(
                broker.call(
                    "desktop.type", {**common, "text": "fixture_hold_for_kill"}, context, 10
                )
            )
            for _ in range(200):
                state = await asyncio.to_thread(guard_request, guardian.config, "guard.status")
                if state["held_count"] == 2:
                    break
                await asyncio.sleep(0.01)
            else:
                raise RuntimeError("fixture did not observe held input")
        print(
            json.dumps(
                {
                    "agent_pid": identity.pid,
                    "agent_created": identity.created,
                    "guardian_pid": guardian.config.guardian_pid,
                    "guardian_created": guardian.config.guardian_created,
                    "cleanup_directory": guardian.config.cleanup_directory,
                }
            ),
            flush=True,
        )
        if work is not None:
            await work
        else:
            await asyncio.sleep(90)
    finally:
        await broker.finish_stop()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--window-pid", type=int)
    args = parser.parse_args()
    asyncio.run(run(args.root, args.window_pid))


if __name__ == "__main__":
    main()
