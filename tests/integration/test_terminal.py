import asyncio
import os
import sys
import time
import uuid
from typing import Any

import httpx2
import psutil
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def operation(
    live: dict[str, Any], name: str, payload: dict[str, Any], *, key: str | None = None
) -> dict[str, Any]:
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "terminal." + name,
            "payload": payload,
            "idempotency_key": key or uuid.uuid4().hex,
            "execution_profile_id": "trusted_personal",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def collect(
    live: dict[str, Any], handle: str, cursor: str = "0", *, until: str = ""
) -> tuple[str, str]:
    chunks: list[str] = []
    for _ in range(100):
        result = await operation(
            live, "read", {"handle_id": handle, "cursor": cursor, "wait_ms": 100}
        )
        assert result["state"] == "SUCCEEDED", result
        output = result["result"]
        chunks.append(output["data"])
        cursor = output["next_cursor"]
        if until and until in "".join(chunks) or output["eof"]:
            return "".join(chunks), cursor
    raise AssertionError("terminal did not produce expected output: " + repr("".join(chunks)))


async def test_pty_01_persistent_repl_resize_independent_cursor_reconnect_and_close(
    live: dict[str, Any],
) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await operation(live, "open", {"argv": [python, "-q", "-i"], "cols": 120, "rows": 40})
    assert opened["state"] == "SUCCEEDED", opened
    info = opened["result"]
    handle = info["handle_id"]
    _, cursor = await collect(live, handle, until=">>>")
    first_input = {"handle_id": handle, "data": "racp_counter = 41\r"}
    accepted = await operation(live, "write", first_input, key="repl-assignment")
    assert accepted["state"] == "SUCCEEDED", accepted
    assert accepted["result"]["accepted_bytes"] == len(first_input["data"].encode())
    assert await operation(live, "write", first_input, key="repl-assignment") == accepted
    resized = await operation(live, "resize", {"handle_id": handle, "cols": 90, "rows": 25})
    assert resized["result"]["cols"] == 90
    await live["restart_gateway"]()
    recovered_handle = (await live["client"].get("/api/v1/handles/" + handle)).json()
    assert recovered_handle["state"] == "ACTIVE"
    assert recovered_handle["agent_boot_id"] == live["agent"].boot_id
    assert recovered_handle["availability"] == "available"
    second_input = {
        "handle_id": handle,
        "data": "print('RACP-' + 'RESULT:', racp_counter + 1, '한글🙂')\r",
    }
    assert (await operation(live, "write", second_input))["state"] == "SUCCEEDED"
    output, end = await collect(live, handle, cursor, until="RACP-RESULT: 42 한글🙂")
    assert "RACP-RESULT: 42 한글🙂" in output, repr(output)
    independent = await operation(live, "read", {"handle_id": handle, "cursor": cursor})
    assert independent["result"]["data"].startswith(output)
    assert int(independent["result"]["next_cursor"]) >= int(end)
    session = live["agent"].terminals.sessions[handle]
    expiry = session.expires
    await operation(live, "read", {"handle_id": handle, "cursor": end})
    assert session.expires == expiry
    closed = await operation(live, "close", {"handle_id": handle})
    assert closed["result"]["cleanup_status"] == "complete", closed
    assert not psutil.pid_exists(info["pid"])
    again = await operation(live, "close", {"handle_id": handle})
    assert again["result"] == closed["result"]
    assert (await live["client"].get("/api/v1/handles/" + handle)).json()["state"] == "CLOSED"
    rejected = await operation(live, "write", {"handle_id": handle, "data": "x"})
    assert rejected["error"]["code"] == "HANDLE_EXPIRED"


async def test_terminal_idle_expiry_terminates_owned_child_tree(live: dict[str, Any]) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    script = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)']); "
        "print('CHILD:'+str(p.pid),flush=True); time.sleep(300)"
    )
    opened = await operation(live, "open", {"argv": [python, "-u", "-c", script]})
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    parent = opened["result"]["pid"]
    _, _ = await collect(live, handle, until="CHILD:")
    children = psutil.Process(parent).children(recursive=True)
    assert children
    session = live["agent"].terminals.sessions[handle]
    session.expires = time.monotonic() - 1
    await live["agent"].terminals.cleanup(expired_only=True)
    assert session.state == "EXPIRED" and session.eof
    assert not psutil.pid_exists(parent)
    assert all(not child.is_running() for child in children)


async def test_terminal_shell_cd_and_python_repl(live: dict[str, Any]) -> None:
    opened = await operation(live, "open", {})
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    prompt = "PS " if os.name == "nt" else "bash"
    _, cursor = await collect(live, handle, until=prompt)
    (live["workspace"] / "test-dir").mkdir()
    change = "Set-Location -LiteralPath 'test-dir'\r" if os.name == "nt" else "cd 'test-dir'\n"
    await operation(live, "write", {"handle_id": handle, "data": change})
    python = getattr(sys, "_base_executable", sys.executable)
    command = f"& '{python}' -q -i\r" if os.name == "nt" else f"'{python}' -q -i\n"
    await operation(live, "write", {"handle_id": handle, "data": command})
    _, cursor = await collect(live, handle, cursor, until=">>>")
    await operation(
        live,
        "write",
        {
            "handle_id": handle,
            "data": "import os; print('RACP-' + 'SHELL:', 6 * 7, os.path.basename(os.getcwd()))\r",
        },
    )
    text, _ = await collect(live, handle, cursor, until="RACP-SHELL: 42")
    assert "RACP-SHELL: 42 test-dir" in text
    await operation(live, "close", {"handle_id": handle})


async def test_terminal_raw_write_ring_overflow_and_lease_cleanup(live: dict[str, Any]) -> None:
    import base64

    python = getattr(sys, "_base_executable", sys.executable)
    opened = await operation(live, "open", {"argv": [python, "-q", "-i"]})
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    await collect(live, handle, until=">>>")
    session = live["agent"].terminals.sessions[handle]
    session.buffer.capacity = 128
    raw = "print('한글' * 100)\r".encode()
    written = await operation(
        live,
        "write",
        {
            "handle_id": handle,
            "data": base64.b64encode(raw).decode(),
            "encoding": "base64",
        },
    )
    assert written["result"]["accepted_bytes"] == len(raw)
    for _ in range(100):
        if session.buffer.earliest > 0:
            break
        await asyncio.sleep(0.01)
    expired = await operation(live, "read", {"handle_id": handle, "cursor": "0"})
    assert expired["error"]["code"] == "CURSOR_EXPIRED", expired
    assert int(expired["error"]["details"]["lost_bytes"]) > 0
    await live["agent"].socket.close()
    live["agent"].lease_expires = time.monotonic() - 1
    for _ in range(100):
        if session.eof and session.state == "EXPIRED":
            break
        await asyncio.sleep(0.01)
    assert session.state == "EXPIRED" and session.eof
    assert not psutil.pid_exists(opened["result"]["pid"])


async def test_state_01_agent_restart_expires_terminal_without_reexecuting_open(
    live: dict[str, Any],
) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    payload = {"argv": [python, "-q", "-i"]}
    opened = await operation(live, "open", payload, key="volatile-open")
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    boot = live["agent"].boot_id
    await live["restart_agent"]()
    expired = (await live["client"].get("/api/v1/handles/" + handle)).json()
    assert expired["state"] == "EXPIRED" and expired["agent_boot_id"] == boot
    assert not psutil.pid_exists(opened["result"]["pid"])
    replay = await operation(live, "open", payload, key="volatile-open")
    assert replay == opened
    read = await operation(live, "read", {"handle_id": handle})
    assert read["error"]["code"] == "HANDLE_EXPIRED", read
    assert not live["agent"].terminals.sessions


async def test_mcp_terminal_uses_registry_and_authenticated_handle(live: dict[str, Any]) -> None:
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + live["owner"]}) as http:
        async with Client(
            streamable_http_client(live["url"] + "/mcp/", http_client=http)
        ) as client:
            opened = await client.call_tool(
                "terminal_open",
                {
                    "device_id": live["device_id"],
                    "idempotency_key": "mcp-terminal",
                    "execution_profile_id": "trusted_personal",
                    "argv": [getattr(sys, "_base_executable", sys.executable), "-q", "-i"],
                },
            )
            assert not opened.is_error, opened
            handle = opened.structured_content["result"]["handle_id"]
            # The first ConPTY chunk can contain only terminal mode sequences.
            # Follow the cursor until the REPL prompt is actually observed.
            cursor, output = "0", ""
            for _ in range(10):
                read = await client.call_tool(
                    "terminal_read",
                    {
                        "device_id": live["device_id"],
                        "handle_id": handle,
                        "cursor": cursor,
                        "wait_ms": 500,
                    },
                )
                assert not read.is_error, read
                chunk = read.structured_content["result"]
                output += chunk["data"]
                cursor = chunk["next_cursor"]
                if ">>>" in output:
                    break
            assert ">>>" in output, output
            closed = await client.call_tool(
                "terminal_close",
                {
                    "device_id": live["device_id"],
                    "handle_id": handle,
                    "idempotency_key": "mcp-close",
                    "execution_profile_id": "trusted_personal",
                },
            )
            assert not closed.is_error and closed.structured_content["result"]["state"] == "CLOSED"


async def test_terminal_read_timeout_preserves_live_session(live: dict[str, Any]) -> None:
    opened = await operation(
        live, "open", {"argv": [getattr(sys, "_base_executable", sys.executable), "-q", "-i"]}
    )
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    _, cursor = await collect(live, handle, until=">>>")
    response = await live["client"].post(
        "/api/v1/operations",
        json={
            "device_id": live["device_id"],
            "operation": "terminal.read",
            "payload": {"handle_id": handle, "cursor": cursor, "wait_ms": 20000},
            "timeout_ms": 50,
            "execution_profile_id": "read_only",
        },
    )
    result = response.json()
    assert result["state"] == "TIMED_OUT", result
    assert live["agent"].terminals.sessions[handle].state == "OPEN"
    written = await operation(
        live, "write", {"handle_id": handle, "data": "print('AFTER-' + 'TIMEOUT')\r"}
    )
    assert written["state"] == "SUCCEEDED"
    output, _ = await collect(live, handle, cursor, until="AFTER-TIMEOUT")
    assert "AFTER-TIMEOUT" in output
    await operation(live, "close", {"handle_id": handle})


async def test_terminal_eof_preserves_nonzero_process_exit(live: dict[str, Any]) -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    opened = await operation(
        live,
        "open",
        {
            "argv": [python, "-u", "-c", "import sys; print('EOF-MARKER'); sys.exit(259)"],
        },
    )
    assert opened["state"] == "SUCCEEDED", opened
    handle = opened["result"]["handle_id"]
    output, cursor = await collect(live, handle)
    assert "EOF-MARKER" in output
    read = await operation(live, "read", {"handle_id": handle, "cursor": cursor})
    assert read["result"]["eof"]
    assert read["result"]["process_exit"] == (259 if os.name == "nt" else 3)
