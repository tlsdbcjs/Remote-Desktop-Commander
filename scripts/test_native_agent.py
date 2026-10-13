"""Run the real Windows Rust Agent against an isolated HTTPS/WSS Gateway.

No GUI, service installation, default profile, or existing device is used.
Generated credentials stay in the fresh run directory and are never printed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import hashlib
import ipaddress
import json
import msvcrt
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psutil
import pywintypes
import win32con
import win32file
import win32pipe
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def require(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait_for(check: Any, timeout: float = 30) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise TimeoutError("condition did not become true within the test budget")


def wait_for_terminal_marker(
    read: Callable[[str, int, float], httpx.Response],
    marker: str,
    timeout: float = 10,
) -> str:
    deadline = time.monotonic() + timeout
    cursor, output = "0", ""
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait_ms = max(1, min(1000, int(remaining * 1000)))
        response = read(cursor, wait_ms, remaining)
        if time.monotonic() >= deadline:
            raise TimeoutError("terminal response arrived after the test budget")
        require(
            response.status_code == 200 and response.json()["state"] == "SUCCEEDED",
            "terminal.read failed while collecting output",
        )
        result = response.json()["result"]
        data, next_cursor = result["data"], result["next_cursor"]
        require(
            isinstance(data, str) and isinstance(next_cursor, str) and next_cursor.isdecimal(),
            "invalid terminal read result",
        )
        require(
            int(next_cursor) >= int(cursor) and (not data or int(next_cursor) > int(cursor)),
            "terminal byte cursor did not advance",
        )
        output += data
        require(
            len(output.encode("utf-8")) <= 128 * 1024, "terminal output collection budget exceeded"
        )
        if marker in output:
            return output
        require(not result.get("eof", False), "terminal ended before expected output")
        if next_cursor == cursor:
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        cursor = next_cursor
    raise TimeoutError("terminal marker did not arrive within the test budget")


class Pipe:
    def __init__(self, root: Path, pid: int):
        endpoint = json.loads((root / "native-endpoint.json").read_text())
        require(endpoint["server_pid"] == pid, "endpoint is not the test Agent")
        require(
            endpoint["pipe_name"].startswith("\\\\.\\pipe\\RACP.Native."), "invalid pipe namespace"
        )
        win32pipe.WaitNamedPipe(endpoint["pipe_name"], 5000)
        self.handle = win32file.CreateFile(
            endpoint["pipe_name"],
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            0,
            None,
            win32con.OPEN_EXISTING,
            0x00100000 | 0x00010000,
            None,
        )
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetNamedPipeServerProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel.GetNamedPipeServerProcessId.restype = ctypes.c_int
        actual_pid = ctypes.c_ulong()
        require(
            kernel.GetNamedPipeServerProcessId(int(self.handle), ctypes.byref(actual_pid))
            and actual_pid.value == pid,
            "named pipe belongs to a different process",
        )
        challenge = self.read()
        require(challenge["protocol"] == 1 and challenge["server_pid"] == pid, "invalid challenge")
        require(challenge["session_id"] == endpoint["session_id"], "wrong session")
        proof = {"protocol": 1, "nonce": challenge["nonce"], "client_nonce": secrets.token_hex(32)}
        self.write(proof)
        require(self.read() == proof, "handshake proof not echoed")

    def close(self) -> None:
        self.handle.Close()

    def read_bytes(self, size: int) -> bytes:
        data = b""
        deadline = time.monotonic() + 10
        while len(data) < size:
            if time.monotonic() >= deadline:
                raise TimeoutError("named pipe read timed out")
            available = win32pipe.PeekNamedPipe(self.handle, 0)[1]
            if not available:
                time.sleep(0.01)
                continue
            _, block = win32file.ReadFile(self.handle, min(size - len(data), available))
            require(block, "named pipe closed mid-frame")
            data += block
        return data

    def read(self) -> dict[str, Any]:
        size = struct.unpack("<I", self.read_bytes(4))[0]
        require(0 < size <= 65536, "invalid control frame length")
        return json.loads(self.read_bytes(size))

    def write(self, value: dict[str, Any]) -> None:
        raw = json.dumps(value, separators=(",", ":")).encode()
        require(len(raw) <= 65536, "test frame too large")
        win32file.WriteFile(self.handle, struct.pack("<I", len(raw)) + raw)

    def request(self, command: dict[str, Any]) -> dict[str, Any]:
        request_id = "req_" + secrets.token_hex(12)
        self.write(
            {"protocol": 1, "request_id": request_id, "deadline_ms": 5000, "command": command}
        )
        reply = self.read()
        require(reply["request_id"] == request_id, "control reply belongs to a different request")
        return reply["result"]


def tls_files(root: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RACP isolated native test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (root / "test-ca.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (root / "test-server.pem").write_bytes(server.public_bytes(serialization.Encoding.PEM))
    (root / "test-tls.key").write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    agent_exe, root = args.agent.resolve(strict=True), args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace, state, gateway_root = root / "workspace", root / "native-state", root / "gateway"
    workspace.mkdir()
    gateway_root.mkdir()
    (workspace / "read.txt").write_text("native-agent-한글-fixture", encoding="utf-8")
    (workspace / "approval.txt").write_text("approval-fixture", encoding="utf-8")
    (root / "outside.txt").write_text("outside-workspace-fixture", encoding="utf-8")
    result: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "agent": str(agent_exe),
        "agent_sha256": hashlib.sha256(agent_exe.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "transport": "real HTTPS/WSS and Windows authenticated Named Pipe",
        "cases": [],
        "existing_profiles_used": False,
    }
    owner = secrets.token_urlsafe(32)
    from racp_gateway.store import GatewayStore
    from racp_sdk.security import digest

    store = GatewayStore(gateway_root / "gateway.db")
    store.initialize(digest(owner))
    store.close()
    tls_files(root)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    gateway_log = (root / "gateway.log").open("w", encoding="utf-8")
    agent_log = (root / "agent.log").open("w", encoding="utf-8")
    gateway = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "racp_gateway.main",
            "--data-dir",
            str(gateway_root),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--public-origin",
            origin,
            "--tls-cert",
            str(root / "test-server.pem"),
            "--tls-key",
            str(root / "test-tls.key"),
            "--client-ca-file",
            str(root / "test-ca.pem"),
            "--enable-trusted-personal",
        ],
        stdout=gateway_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    agent = None
    subscription = None
    event_stop = threading.Event()
    events: list[dict[str, Any]] = []

    def passed(name: str, **evidence: Any) -> None:
        result["cases"].append({"name": name, "passed": True, **evidence})
        print("PASS " + name, flush=True)

    def control(command: dict[str, Any]) -> dict[str, Any]:
        require(agent is not None and agent.poll() is None, "test Agent is not running")
        pipe = Pipe(state, agent.pid)
        try:
            return pipe.request(command)
        finally:
            pipe.close()

    try:
        with httpx.Client(
            base_url=origin,
            verify=str(root / "test-ca.pem"),
            timeout=35,
            headers={"Authorization": "Bearer " + owner},
            trust_env=False,
        ) as client:

            def ready() -> bool:
                require(gateway.poll() is None, "isolated Gateway exited; inspect gateway.log")
                try:
                    return client.get("/readyz").status_code == 200
                except httpx.TransportError:
                    return False

            wait_for(ready)
            require(
                client.get("/readyz", headers={"Authorization": ""}).status_code == 401,
                "unauthenticated owner API accepted",
            )
            passed("gateway_https_owner_auth")
            enrollment = client.post(
                "/api/v1/enrollment-tokens",
                json={"name": "native-smoke", "include_connection_file": True},
            )
            require(enrollment.status_code == 200, "test enrollment issuance failed")
            connection_path = root / "connection.json"
            connection_path.write_text(
                json.dumps(enrollment.json()["connection_file"]), encoding="utf-8"
            )
            policy = {
                "schema_version": 1,
                "revision": 0,
                "grants": {
                    "diagnostics.agent.read": "allow",
                    "files.read.text": "allow",
                    "files.list": "allow",
                    "exec.argv": "allow",
                    "terminal.open": "allow",
                    "terminal.input.write": "allow",
                    "terminal.output.read": "allow",
                    "terminal.resize": "allow",
                    "terminal.close": "allow",
                },
                "disabled_categories": [],
                "constraints": {
                    "workspace_ids": [],
                    "max_timeout_ms": 120000,
                    "max_output_bytes": 1048576,
                    "executable_allowlist": [],
                    "executable_denylist": [],
                    "strict_os_isolation": False,
                },
            }
            enroll_input = {
                "connection_file": str(connection_path),
                "connection_sha256": hashlib.sha256(connection_path.read_bytes()).hexdigest(),
                "workspace": str(workspace),
                "allowed_workspaces": [],
                "profile": "trusted_personal",
                "policy": policy,
            }
            registered = subprocess.run(
                [str(agent_exe), "native-enroll", "--state-dir", str(state)],
                input=json.dumps(enroll_input),
                text=True,
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            require(
                registered.returncode == 0,
                f"native-enroll failed: {registered.stdout.strip()} {registered.stderr.strip()}",
            )
            registered_result = json.loads(registered.stdout)
            require(
                registered_result["ok"] and registered_result["result"]["registered"],
                "native registration was not committed",
            )
            device_id = registered_result["result"]["device_id"]
            require((state / "native-credential.bin").exists(), "native DPAPI credential missing")
            require(
                client.post(
                    "/agent/v1/enroll", json={"token": enrollment.json()["token"]}
                ).status_code
                == 401,
                "consumed enrollment token accepted again",
            )
            passed("native_registration_dpapi_and_single_use_token", device_id=device_id)
            agent = subprocess.Popen(
                [str(agent_exe), "native-run", "--state-dir", str(state)],
                stdout=agent_log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            def online() -> Any:
                require(agent.poll() is None, "Rust Agent exited; inspect agent.log")
                device = client.get("/api/v1/devices/" + device_id).json()
                return device if device.get("info", {}).get("status") == "ONLINE" else None

            device = wait_for(online)
            version = subprocess.check_output([str(agent_exe), "--version"], text=True).strip()
            require(
                device["info"]["agent_version"] == version.split()[-1],
                "Gateway reports a different Agent version",
            )
            passed("real_agent_wss_online", version=version, pid=agent.pid)
            snapshot = control({"type": "snapshot"})
            require(
                snapshot["type"] == "snapshot" and snapshot["role"] == "controller",
                "authenticated control snapshot failed",
            )
            boot_id = snapshot["snapshot"]["boot_id"]
            passed("named_pipe_authenticated_snapshot", boot_id=boot_id)

            def cli_control(command: dict[str, Any]) -> dict[str, Any]:
                request_id = "cli_" + secrets.token_hex(12)
                completed = subprocess.run(
                    [str(agent_exe), "native-control", "--state-dir", str(state)],
                    input=json.dumps(
                        {
                            "protocol": 1,
                            "request_id": request_id,
                            "deadline_ms": 5000,
                            "command": command,
                        }
                    ),
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                require(
                    completed.returncode == 0,
                    "headless control CLI failed: " + completed.stderr.strip(),
                )
                reply = json.loads(completed.stdout)
                require(reply["request_id"] == request_id, "headless receipt identity mismatch")
                return reply["result"]

            cli_snapshot = cli_control({"type": "snapshot"})
            require(
                cli_snapshot["type"] == "snapshot" and cli_snapshot["role"] == "controller",
                "headless CLI did not authenticate as controller",
            )
            require(
                cli_snapshot["snapshot"]["boot_id"] == boot_id,
                "headless CLI selected a different Agent boot",
            )
            passed("headless_cli_authenticated_snapshot")

            def execute(
                operation: str, payload: dict[str, Any], *, http_timeout: float = 35, **extra: Any
            ) -> httpx.Response:
                return client.post(
                    "/api/v1/operations",
                    json={
                        "device_id": device_id,
                        "operation": operation,
                        "payload": payload,
                        "execution_profile_id": "trusted_personal",
                        "idempotency_key": "smoke-" + secrets.token_hex(8),
                        **extra,
                    },
                    timeout=http_timeout,
                )

            watcher = subprocess.Popen(
                [str(agent_exe), "native-control", "--state-dir", str(state)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                require(
                    watcher.stdin is not None and watcher.stdout is not None,
                    "headless subscription pipes missing",
                )
                watcher.stdin.write(
                    json.dumps(
                        {
                            "protocol": 1,
                            "request_id": "cli_watch_1",
                            "deadline_ms": 5000,
                            "command": {
                                "type": "subscribe",
                                "boot_id": boot_id,
                                "after_sequence": snapshot["snapshot"]["sequence"],
                            },
                        }
                    )
                )
                watcher.stdin.close()
                wait_for(
                    lambda: (
                        win32pipe.PeekNamedPipe(msvcrt.get_osfhandle(watcher.stdout.fileno()), 0)[1]
                        > 0
                    ),
                    10,
                )
                subscribed_cli = json.loads(watcher.stdout.readline())
                require(
                    subscribed_cli["result"]["type"] == "subscribed",
                    "headless subscription was rejected",
                )
                require(
                    execute("filesystem.read", {"path": "read.txt"}).json()["state"] == "SUCCEEDED",
                    "headless watched remote read failed",
                )
                require(watcher.wait(timeout=8) == 0, "headless watch did not end within its bound")
                streamed = [json.loads(line) for line in watcher.stdout.read().splitlines()]
                require(
                    streamed and all(event["boot_id"] == boot_id for event in streamed),
                    "headless watch emitted no events or another boot's events",
                )
                require(
                    all(
                        event["sequence"] > subscribed_cli["result"]["snapshot"]["sequence"]
                        for event in streamed
                    ),
                    "headless stream sequence did not advance",
                )
                passed("headless_cli_subscription_activity", event_count=len(streamed))
            finally:
                if watcher.poll() is None:
                    watcher.kill()
                    watcher.wait(timeout=10)
                for stream in (watcher.stdin, watcher.stdout, watcher.stderr):
                    if stream is not None:
                        stream.close()

            diagnostic = execute("diagnostics.agent", {}).json()
            require(diagnostic["state"] == "SUCCEEDED", "live Agent diagnosis failed")
            report = diagnostic["result"]
            require(report["agent_boot_id"] == boot_id, "diagnostics reported another Agent boot")
            require(
                report["health"]["observed"]
                and report["health"]["gateway_phase"] == "ready"
                and report["health"]["execution_lease_valid"],
                "diagnostics did not observe the live Gateway execution lease",
            )
            require(
                report["health"]["active_operations"] >= 1, "diagnostics missed its active request"
            )
            require("journal" in report["health"], "diagnostics omitted bounded queue/spool counts")
            encoded = json.dumps(report)
            require(len(encoded.encode()) <= 8192, "diagnostics exceeded its fixed response budget")
            for secret in (owner, str(state), str(workspace), origin):
                require(secret not in encoded, "diagnostics exposed a secret or local path")
            passed("bounded_redacted_live_agent_diagnostics")

            read = execute("filesystem.read", {"path": "read.txt"})
            require(
                read.status_code == 200 and read.json()["state"] == "SUCCEEDED",
                f"file read failed: {read.text}",
            )
            require(
                "native-agent-한글-fixture" in json.dumps(read.json(), ensure_ascii=False),
                "UTF-8 file result missing",
            )
            passed("remote_utf8_file_read")
            denied = execute(
                "filesystem.write", {"path": "must-not-exist.txt", "content": "denied"}
            )
            require(
                denied.status_code == 200
                and denied.json()["state"] == "FAILED"
                and denied.json()["error"]["code"] == "PERMISSION_DENIED",
                f"local default deny failed: {denied.text}",
            )
            require(
                not (workspace / "must-not-exist.txt").exists(),
                "denied write changed the workspace",
            )
            passed("local_permission_deny_no_side_effect")
            escape = execute("filesystem.read", {"path": "../outside.txt"})
            require(
                escape.status_code == 200 and escape.json()["state"] == "FAILED",
                "workspace traversal accepted",
            )
            require("outside-workspace-fixture" not in escape.text, "outside contents leaked")
            passed("workspace_escape_rejected")
            shell = execute(
                "shell.exec",
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        "import sys; print('native-exec'); "
                        "print('native-stderr',file=sys.stderr); sys.exit(7)",
                    ]
                },
            )
            require(
                shell.status_code == 200 and shell.json()["state"] == "SUCCEEDED",
                f"shell execution failed: {shell.text}",
            )
            output = shell.json()["result"]
            require(
                output["stdout"].strip() == "native-exec"
                and output["stderr"].strip() == "native-stderr"
                and output["exit_code"] == 7,
                "shell output/exit code mismatched",
            )
            require(output["cleanup_status"] == "complete", "shell cleanup incomplete")
            passed("real_child_execution_stdout_stderr_exit_and_cleanup")
            once_request = {
                "device_id": device_id,
                "operation": "shell.exec",
                "execution_profile_id": "trusted_personal",
                "idempotency_key": "native-smoke-exactly-once",
                "payload": {
                    "argv": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; p=Path('once.counter'); "
                        "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')",
                    ]
                },
            }
            once_first = client.post("/api/v1/operations", json=once_request)
            once_second = client.post("/api/v1/operations", json=once_request)
            require(
                once_first.status_code == once_second.status_code == 200
                and once_first.json()["state"] == "SUCCEEDED",
                "idempotent shell request failed",
            )
            require(
                once_first.json()["operation_id"] == once_second.json()["operation_id"]
                and (workspace / "once.counter").read_text() == "1",
                "duplicate request executed its mutation more than once",
            )
            passed("idempotent_request_executes_mutation_once")
            terminal_open = execute(
                "terminal.open",
                {"argv": [str(Path(os.environ["SYSTEMROOT"]) / "System32/cmd.exe"), "/d", "/q"]},
            )
            require(
                terminal_open.status_code == 200 and terminal_open.json()["state"] == "SUCCEEDED",
                f"ConPTY terminal open failed: {terminal_open.text}",
            )
            terminal = terminal_open.json()["result"]
            terminal_target = {"handle_id": terminal["handle_id"], "agent_boot_id": boot_id}
            terminal_write = execute(
                "terminal.write",
                {
                    **terminal_target,
                    "data": "echo native-terminal-output\r\n"
                    "echo native-terminal-file>pty.marker\r\n",
                },
            )
            require(
                terminal_write.status_code == 200 and terminal_write.json()["state"] == "SUCCEEDED",
                "terminal input failed",
            )
            wait_for(lambda: (workspace / "pty.marker").exists(), 10)
            require(
                (workspace / "pty.marker").read_text().strip() == "native-terminal-file",
                "terminal command did not execute",
            )
            wait_for_terminal_marker(
                lambda cursor, wait_ms, remaining: execute(
                    "terminal.read",
                    {**terminal_target, "cursor": cursor, "wait_ms": wait_ms},
                    http_timeout=remaining,
                ),
                "native-terminal-output",
            )
            resized = execute("terminal.resize", {**terminal_target, "cols": 100, "rows": 30})
            require(
                resized.status_code == 200 and resized.json()["state"] == "SUCCEEDED",
                "terminal resize failed",
            )
            passed("real_conpty_terminal_input_output_resize", pid=terminal["pid"])
            closed = execute("terminal.close", terminal_target)
            require(
                closed.status_code == 200
                and closed.json()["state"] == "SUCCEEDED"
                and closed.json()["result"]["cleanup_status"] == "complete",
                "terminal close cleanup failed",
            )
            wait_for(lambda: not psutil.pid_exists(terminal["pid"]), 5)
            passed("terminal_close_owned_process_is_gone")

            subscription = Pipe(state, agent.pid)
            subscribed = subscription.request(
                {"type": "subscribe", "boot_id": boot_id, "after_sequence": 0}
            )
            require(subscribed["type"] == "subscribed", "controller subscription failed")

            def consume_events() -> None:
                while not event_stop.is_set():
                    try:
                        events.append(subscription.read())
                    except (OSError, TimeoutError, pywintypes.error):
                        return

            threading.Thread(target=consume_events, daemon=True).start()
            update = cli_control(
                {
                    "type": "update_policy",
                    "expected_revision": 0,
                    "changes": {"grants": {"files.read.text": "require_approval"}},
                }
            )
            require(update["type"] == "accepted", f"hot policy update not accepted: {update}")
            update_result = wait_for(
                lambda: next(
                    (
                        e["change"]
                        for e in events
                        if e.get("change", {}).get("receipt_id") == update["receipt_id"]
                    ),
                    None,
                ),
                10,
            )
            require(
                update_result["type"] == "policy_finished"
                and update_result["committed"]
                and update_result["revision"] == 1,
                f"hot policy commit failed: {update_result}",
            )
            conflict = cli_control(
                {
                    "type": "update_policy",
                    "expected_revision": 0,
                    "changes": {"grants": {"files.read.text": "allow"}},
                }
            )
            require(conflict["type"] == "accepted", "policy conflict receipt not accepted")
            conflict_result = wait_for(
                lambda: next(
                    (
                        e["change"]
                        for e in events
                        if e.get("change", {}).get("receipt_id") == conflict["receipt_id"]
                    ),
                    None,
                ),
                10,
            )
            require(
                not conflict_result["committed"]
                and conflict_result["retirement_state"] == "REVISION_CONFLICT",
                "stale policy CAS committed",
            )
            passed("hot_policy_commit_and_revision_conflict")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as workers:
                future = workers.submit(execute, "filesystem.read", {"path": "approval.txt"})
                prompt = wait_for(
                    lambda: next(
                        (
                            e["change"]
                            for e in events
                            if e.get("change", {}).get("type") == "approval_pending"
                        ),
                        None,
                    ),
                    10,
                )
                decision = cli_control(
                    {"type": "decide_approval", "nonce": prompt["nonce"], "allow": True}
                )
                require(decision["type"] == "accepted", "bound approval decision rejected")
                approved = future.result(timeout=15)
                require(
                    approved.status_code == 200
                    and approved.json()["state"] == "SUCCEEDED"
                    and "approval-fixture" in approved.text,
                    f"approved read failed: {approved.text}",
                )
                replay = cli_control(
                    {"type": "decide_approval", "nonce": prompt["nonce"], "allow": True}
                )
                require(replay["type"] == "error", "approval nonce replay accepted")
            passed("local_approval_execute_and_nonce_replay_rejection")
            seen_nonces = {e.get("change", {}).get("nonce") for e in events}
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as workers:
                future = workers.submit(execute, "filesystem.read", {"path": "approval.txt"})
                prompt = wait_for(
                    lambda: next(
                        (
                            e["change"]
                            for e in events
                            if e.get("change", {}).get("type") == "approval_pending"
                            and e["change"]["nonce"] not in seen_nonces
                        ),
                        None,
                    ),
                    10,
                )
                require(
                    cli_control(
                        {"type": "decide_approval", "nonce": prompt["nonce"], "allow": False}
                    )["type"]
                    == "accepted",
                    "headless deny decision was rejected",
                )
                denied_approval = future.result(timeout=15)
                require(
                    denied_approval.json()["state"] == "FAILED"
                    and denied_approval.json()["error"]["code"] == "APPROVAL_REQUIRED"
                    and denied_approval.json()["result"] is None
                    and "approval-fixture" not in denied_approval.text,
                    "denied approval exposed the target file",
                )
                denied_event = wait_for(
                    lambda: next(
                        (
                            e["change"]
                            for e in events
                            if e.get("change", {}).get("type") == "approval_finished"
                            and e["change"]["nonce"] == prompt["nonce"]
                        ),
                        None,
                    ),
                    10,
                )
                require(
                    denied_event["state"] == "denied_or_expired",
                    "headless denial did not finish the approval",
                )
            passed("headless_approval_denial_no_data")

            job = execute(
                "shell.exec",
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        "import os,time; from pathlib import Path; "
                        "Path('job.pid').write_text(str(os.getpid())); "
                        "print('started',flush=True); time.sleep(45)",
                    ]
                },
                execution_mode="job",
                timeout_ms=60000,
            )
            require(job.status_code == 202, f"async job not accepted: {job.text}")
            operation_id = job.json()["operation_id"]

            def running_job() -> Any:
                item = client.get("/api/v1/operations/" + operation_id).json()
                return item if item.get("state") == "RUNNING" else None

            wait_for(running_job, 10)
            wait_for(lambda: (workspace / "job.pid").exists(), 10)
            owned_child_pid = int((workspace / "job.pid").read_text())
            require(
                psutil.pid_exists(owned_child_pid), "cancellation fixture child was not running"
            )
            activity = wait_for(
                lambda: next(
                    (
                        item
                        for item in cli_control({"type": "snapshot"})["snapshot"]["activity"]
                        if item["operation_id"] == operation_id
                    ),
                    None,
                ),
                10,
            )
            stale_cancel = cli_control(
                {
                    "type": "cancel_operation",
                    "boot_id": "boot_stale",
                    "operation_id": operation_id,
                    "target_request_id": activity["request_id"],
                }
            )
            require(
                stale_cancel["type"] == "error" and psutil.pid_exists(owned_child_pid),
                "stale headless cancellation affected the child",
            )
            cancelled = cli_control(
                {
                    "type": "cancel_operation",
                    "boot_id": boot_id,
                    "operation_id": operation_id,
                    "target_request_id": activity["request_id"],
                }
            )
            require(cancelled["type"] == "accepted", "headless job cancellation not accepted")

            def finished_job() -> Any:
                item = client.get("/api/v1/operations/" + operation_id).json()
                return item if item.get("state") in {"CANCELLED", "FAILED", "SUCCEEDED"} else None

            terminal = wait_for(finished_job, 15)
            require(
                terminal["state"] == "CANCELLED", f"cancelled job ended as {terminal.get('state')}"
            )
            wait_for(lambda: not psutil.pid_exists(owned_child_pid), 5)
            passed("job_tracking_cancel_and_terminal_result", operation_id=operation_id)
            passed("cancelled_owned_child_process_is_gone", pid=owned_child_pid)
            stopped = control({"type": "stop"})
            require(stopped["type"] == "accepted", "Agent stop not accepted")
            require(agent.wait(timeout=25) == 0, "Agent did not exit successfully")
            lifecycle = json.loads((state / "native-run.json").read_text())
            require(
                lifecycle.get("phase") == "complete" and lifecycle.get("error_code") is None,
                f"Agent cleanup phase was {lifecycle.get('phase')}",
            )
            passed("agent_stop_process_exit_and_durable_cleanup")
            event_stop.set()
            subscription.close()
            subscription = None
            agent = subprocess.Popen(
                [str(agent_exe), "native-run", "--state-dir", str(state)],
                stdout=agent_log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            wait_for(
                lambda: (
                    (state / "native-endpoint.json").exists()
                    and json.loads((state / "native-endpoint.json").read_text()).get("server_pid")
                    == agent.pid
                ),
                10,
            )
            wait_for(
                lambda: (
                    client.get("/api/v1/devices/" + device_id).json().get("epoch", 0)
                    > device["epoch"]
                ),
                15,
            )
            wait_for(online)
            restarted = control({"type": "snapshot"})["snapshot"]
            require(
                restarted["boot_id"] != boot_id and restarted["policy_revision"] == 1,
                "restart did not preserve policy or assign a new boot identity",
            )
            require(
                restarted["policy"]["grants"]["files.read.text"] == "require_approval",
                "policy grant was lost on restart",
            )
            require(
                (workspace / "once.counter").read_text() == "1", "restart reran completed mutation"
            )
            passed("agent_restart_reconnect_preserves_policy_and_workspace", pid=agent.pid)
            unavailable = execute("filesystem.read", {"path": "approval.txt"})
            require(
                unavailable.json()["state"] == "FAILED"
                and unavailable.json()["error"]["code"] == "APPROVAL_REQUIRED"
                and unavailable.json()["result"] is None
                and "approval-fixture" not in unavailable.text,
                "request without an approver was automatically allowed",
            )
            passed("approval_without_controller_is_unavailable")
            require(
                cli_control({"type": "stop"})["type"] == "accepted",
                "restarted Agent stop not accepted",
            )
            require(agent.wait(timeout=25) == 0, "restarted Agent did not stop cleanly")
            require(
                json.loads((state / "native-run.json").read_text())["phase"] == "complete",
                "restarted cleanup receipt incomplete",
            )
            result["passed"] = True
    except Exception as error:
        result["passed"] = False
        result["failure"] = {"type": type(error).__name__, "message": str(error)}
        print("FAIL " + str(error), flush=True)
    finally:
        event_stop.set()
        if subscription is not None:
            subscription.close()
        if agent is not None and agent.poll() is None:
            try:
                control({"type": "stop"})
                agent.wait(timeout=25)
            except Exception:
                result["forced_test_agent_cleanup"] = True
                agent.kill()
                agent.wait(timeout=10)
        gateway.terminate()
        gateway.wait(timeout=10)
        agent_log.close()
        gateway_log.close()
        result["finished_at"] = datetime.now(UTC).isoformat()
        require(
            result["harness_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "test harness changed during execution",
        )
        (root / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
