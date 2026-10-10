import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import psutil
from conftest import shell_request
from racp_protocol.models import Request


async def poll(live: dict[str, Any], operation_id: str) -> dict[str, Any]:
    for _ in range(150):
        result = (await live["client"].get("/api/v1/operations/" + operation_id)).json()
        if result["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "UNKNOWN"}:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError("operation did not reach a terminal state")


async def wait_file(path: Path) -> None:
    for _ in range(100):
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("fixture child did not start")


async def test_life_01_timeout_kills_children_and_grandchildren(live: dict[str, Any]) -> None:
    grandchild = "import time; time.sleep(90)"
    child = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
        "Path('grandchild.tmp').write_text(str(p.pid)); "
        "os.replace('grandchild.tmp','grandchild.pid'); time.sleep(90)"
    )
    parent = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "Path('child.tmp').write_text(str(p.pid)); "
        "os.replace('child.tmp','child.pid'); time.sleep(90)"
    )
    request = shell_request(live, [sys.executable, "-c", parent], timeout_ms=10000)
    pending = asyncio.create_task(live["client"].post("/api/v1/operations", json=request))
    try:
        for name in ("child.pid", "grandchild.pid"):
            await wait_file(live["workspace"] / name)
            pid = int((live["workspace"] / name).read_text())
            assert psutil.pid_exists(pid), "Fixture process must exist before the deadline"
        response = await pending
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    result = response.json()
    assert result["state"] == "TIMED_OUT", result
    assert result["result"]["cleanup_status"] == "complete"
    for name in ("child.pid", "grandchild.pid"):
        pid = int((live["workspace"] / name).read_text())
        assert not psutil.pid_exists(pid), f"owned process leaked: {pid}"
    replay = await live["client"].post("/api/v1/operations", json=request)
    assert replay.json() == result


async def test_job_cancel_confirms_tree_cleanup_and_late_cancel_keeps_result(
    live: dict[str, Any],
) -> None:
    code = (
        "import os,time; from pathlib import Path; "
        "Path('pid').write_text(str(os.getpid())); time.sleep(90)"
    )
    response = await live["client"].post(
        "/api/v1/operations",
        json=shell_request(live, [sys.executable, "-c", code], execution_mode="job"),
    )
    operation_id = response.json()["operation_id"]
    await wait_file(live["workspace"] / "pid")
    pid = int((live["workspace"] / "pid").read_text())
    cancel = await live["client"].post("/api/v1/operations/" + operation_id + "/cancel")
    assert cancel.json()["state"] in {"CANCEL_REQUESTED", "CANCELLED"}
    result = await poll(live, operation_id)
    assert result["state"] == "CANCELLED" and not psutil.pid_exists(pid)
    late = await live["client"].post("/api/v1/operations/" + operation_id + "/cancel")
    assert late.json() == result


async def test_rpc_02_lost_result_reconciles_without_reexecution(live: dict[str, Any]) -> None:
    code = (
        "import time; from pathlib import Path; Path('counter').write_text('1'); "
        "time.sleep(1); print('done')"
    )
    response = await live["client"].post(
        "/api/v1/operations",
        json=shell_request(live, [sys.executable, "-c", code], execution_mode="job"),
    )
    operation_id = response.json()["operation_id"]
    await wait_file(live["workspace"] / "counter")
    connection = live["app"].state.control.connections[live["device_id"]]
    await connection.socket.close(code=1012)
    result = await poll(live, operation_id)
    assert result["state"] == "SUCCEEDED", result
    assert result["result"]["stdout"].strip() == "done"
    assert (live["workspace"] / "counter").read_text() == "1"
    journal_record = live["agent"].journal.get(operation_id)
    request = Request.model_validate(journal_record["request"]).model_copy(
        update={"connection_epoch": live["agent"].epoch}
    )
    await live["agent"].dispatch(request)
    assert (live["workspace"] / "counter").read_text() == "1"


async def test_auth_03_revoke_blocks_new_work_and_cleans_owned_process(
    live: dict[str, Any],
) -> None:
    code = (
        "import os,time; from pathlib import Path; "
        "Path('pid.tmp').write_text(str(os.getpid())); os.replace('pid.tmp','pid'); time.sleep(90)"
    )
    response = await live["client"].post(
        "/api/v1/operations",
        json=shell_request(live, [sys.executable, "-c", code], execution_mode="job"),
    )
    operation_id = response.json()["operation_id"]
    await wait_file(live["workspace"] / "pid")
    pid = int((live["workspace"] / "pid").read_text())
    response = await live["client"].post("/api/v1/devices/" + live["device_id"] + "/revoke")
    assert response.status_code == 200
    for _ in range(100):
        if not psutil.pid_exists(pid):
            break
        await asyncio.sleep(0.05)
    assert not psutil.pid_exists(pid)
    denied = await live["client"].post(
        "/api/v1/operations",
        json=shell_request(live, [sys.executable, "--version"], "new-after-revoke"),
    )
    assert denied.status_code == 403
    # Process exit precedes output drain and the durable terminal journal write.
    for _ in range(100):
        if live["agent"].journal.get(operation_id)["state"] == "CANCELLED":
            break
        await asyncio.sleep(0.05)
    assert live["agent"].journal.get(operation_id)["state"] == "CANCELLED"


async def test_lease_expiry_cleans_execution(live: dict[str, Any]) -> None:
    code = (
        "import os,time; from pathlib import Path; "
        "Path('pid').write_text(str(os.getpid())); time.sleep(90)"
    )
    response = await live["client"].post(
        "/api/v1/operations",
        json=shell_request(live, [sys.executable, "-c", code], execution_mode="job"),
    )
    await wait_file(live["workspace"] / "pid")
    pid = int((live["workspace"] / "pid").read_text())
    live["agent"].lease_expires = time.monotonic() - 1
    result = await poll(live, response.json()["operation_id"])
    assert result["state"] == "CANCELLED" and not psutil.pid_exists(pid)


async def test_invalid_http_schema_does_not_echo_secret(live: dict[str, Any]) -> None:
    secret = "secret-string-do-not-echo"
    response = await live["client"].post("/agent/v1/enroll", json={"token": secret, "extra": 1})
    assert response.status_code == 400 and secret not in response.text
    response = await live["client"].post(
        "/api/v1/operations", content=json.dumps({"x": "a" * 1048577})
    )
    assert response.status_code == 400
