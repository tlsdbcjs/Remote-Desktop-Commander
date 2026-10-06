"""Synthetic RE domain contract server. It is not a real analysis/debugger backend."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from racp_protocol.models import new_id
from racp_protocol.plugins import PluginEvent, PluginResult
from racp_protocol.registry import validate_payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()
    resources: dict[str, dict[str, Any]] = {}
    event_sequence = 0
    for raw in sys.stdin.buffer:
        request = json.loads(raw)
        if request["type"] == "cancel":
            continue
        operation, payload = request["operation"], request["payload"]
        event = None
        error = None
        result: dict[str, Any] = {}
        if operation == "plugin.health":
            result = {
                "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                "backend_version": "fixture-1",
            }
        else:
            payload = validate_payload(operation, payload)
            if operation in {"re.open", "debugger.launch"}:
                target = Path(payload["path" if operation == "re.open" else "executable"])
                id = new_id("backend")
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                resources[id] = {
                    "resource_id": id,
                    "target_sha256": digest,
                    "architecture": "synthetic-x86_64",
                    "image_base": "0x140000000",
                    "names": {},
                    "state": "STOPPED",
                    "sequence": 1,
                }
                result = {
                    k: v
                    for k, v in resources[id].items()
                    if k in {"resource_id", "target_sha256", "architecture", "image_base"}
                }
                if operation == "re.open":
                    result.update(
                        analysis_database=str(target.parent / "fixture.db"), analysis_state="READY"
                    )
                else:
                    result.update(
                        debugger_state="STOPPED",
                        stop_sequence="1",
                        stop_reason="synthetic_initial_stop",
                    )
            else:
                id = payload["analysis_id" if operation.startswith("re.") else "debug_id"]
                resource = resources[id]
                if operation == "re.query":
                    if args.mode == "crash":
                        os._exit(3)
                    if args.mode == "unsafe_file":
                        result = {"spool_path": str(args.manifest)}
                    elif payload["action"] in {"functions", "strings", "xrefs"}:
                        offset = int(payload.get("cursor") or "0")
                        rows = [
                            {
                                "address": hex(0x140001000 + i * 16),
                                "name": resource["names"].get(
                                    hex(0x140001000 + i * 16), "synthetic_" + str(i)
                                ),
                            }
                            for i in range(5)
                        ]
                        end = offset + payload["limit"]
                        result = {
                            "items": rows[offset:end],
                            "next_cursor": str(end) if end < len(rows) else None,
                        }
                    elif payload["action"] == "decompile":
                        error = {
                            "code": "OPERATION_NOT_SUPPORTED",
                            "message": "synthetic backend has no decompiler",
                            "execution_state": "not_started",
                        }
                    else:
                        result = {
                            "architecture": resource["architecture"],
                            "image_base": resource["image_base"],
                            "synthetic": True,
                        }
                elif operation == "re.command":
                    if payload["action"] == "rename":
                        resource["names"][payload["address"]] = payload["name"]
                    result = {"updated": True}
                elif operation == "debugger.command":
                    if payload["action"] in {"continue", "step_into", "step_over"}:
                        resource["sequence"] += 1
                        event_sequence += 1
                        event = PluginEvent(
                            instance_id=request["instance_id"],
                            sequence=str(event_sequence),
                            kind="debugger.stopped",
                            data={
                                "resource_id": id,
                                "debugger_state": "STOPPED",
                                "stop_sequence": str(resource["sequence"]),
                                "stop_reason": "synthetic_breakpoint",
                            },
                        )
                        result = {"accepted": True, "debugger_state": "RUNNING"}
                    else:
                        result = {"breakpoint_id": "bp_fixture", "debugger_state": "STOPPED"}
                elif operation == "debugger.read_memory":
                    result = {"bytes_hex": (b"\x90" * payload["size_bytes"]).hex()}
                elif operation == "debugger.registers":
                    result = {"registers": {"rip": "0x140001000"}}
                elif operation == "debugger.backtrace":
                    result = {"frames": [{"address": "0x140001000", "function": "synthetic_main"}]}
                elif operation.endswith(".close"):
                    result = {"closed": True}
                else:
                    result = {"debugger_state": resource["state"], "synthetic": True}
        reply = PluginResult(
            instance_id=request["instance_id"],
            request_id=request["request_id"],
            state="FAILED" if error else "SUCCEEDED",
            result=None if error else result,
            error=error,
        )
        print(reply.model_dump_json(), flush=True)
        if event is not None:
            print(event.model_dump_json(), flush=True)


if __name__ == "__main__":
    main()
