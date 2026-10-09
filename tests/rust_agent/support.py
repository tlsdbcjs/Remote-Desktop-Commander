"""Shared development fixtures; credentials never appear in subprocess arguments."""

import asyncio
import json
import subprocess
from pathlib import Path

from racp_sdk.security import canonical_digest, digest

ROOT = Path(__file__).resolve().parents[2]


async def bridge(executable: Path, state: Path, action: str, **data):
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "bridge",
        "--state-dir",
        str(state),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output, error = await asyncio.wait_for(
        process.communicate(json.dumps({"action": action, **data}).encode() + b"\n"), 50
    )
    assert not error, "Rust bridge must not emit potentially sensitive diagnostics"
    reply = json.loads(output)
    if not reply.get("ok"):
        code = reply.get("code", "REQUEST_FAILED")
        if action == "start" and code == "RUNTIME_UNAVAILABLE":
            # The background child suppresses diagnostics. An owned foreground
            # retry exposes only a static startup code, never credentials or paths.
            diagnostic = await asyncio.create_subprocess_exec(
                str(executable),
                "serve",
                "--state-dir",
                str(state),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(diagnostic.communicate(), 3)
                candidate = stderr.decode("ascii", errors="ignore").strip()
                if (
                    candidate
                    and len(candidate) <= 64
                    and all(c.isupper() or c == "_" for c in candidate)
                ):
                    code += ":" + candidate
            except TimeoutError:
                await bridge(executable, state, "stop")
                await asyncio.wait_for(diagnostic.communicate(), 5)
                code += ":FOREGROUND_STARTED"
        raise RuntimeError(code)
    assert process.returncode == 0
    return reply["result"]


def operation(live, name, payload, suffix):
    request = {
        "protocol": 1,
        "type": "request",
        "device_id": live["device_id"],
        "agent_boot_id": "boot_fixture",
        "connection_epoch": 1,
        "request_id": "req_" + suffix,
        "operation_id": "op_" + suffix,
        "trace_id": "0" * 32,
        "timestamp": "2026-10-10T00:00:00Z",
        "operation": name,
        "timeout_ms": 120000,
        "remaining_timeout_ms": 120000,
        "execution_mode": "sync",
        "idempotency_key": suffix,
        "context": {
            "principal_id": "owner_local",
            "workspace_id": "default",
            "execution_profile_id": "trusted_personal",
            "policy_revision": 1,
        },
        "payload": payload,
        "retain_key": True,
    }
    store = live["app"].state.control.store
    store.accept(
        digest("owner_local:" + live["device_id"]),
        digest(suffix),
        canonical_digest(request),
        request,
    )
    store.transition(request["operation_id"], "RUNNING")
    return request["operation_id"]
