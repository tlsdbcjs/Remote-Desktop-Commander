"""Inspect native Codex MCP startup without starting a thread or model turn."""

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, CODEX_CA_CERTIFICATE=str(root / ".racp/two-pc-lab/ca.pem"))
    log_path = root / ".racp/two-pc-lab/codex-mcp-startup-private.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "codex -c mcp_servers.node_repl.enabled=false "
                "-c mcp_servers.oracle.enabled=false app-server",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdin and process.stdout
        messages: queue.Queue[dict | None] = queue.Queue()

        def receive() -> None:
            assert process.stdout
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            messages.put(None)

        threading.Thread(target=receive, daemon=True).start()

        def send(message: dict) -> None:
            assert process.stdin
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def response(identifier: int, timeout: float = 60) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    message = messages.get(timeout=1)
                except queue.Empty:
                    continue
                if message is None:
                    raise RuntimeError("Native Codex checker exited before its response")
                if message.get("id") == identifier:
                    if "error" in message:
                        raise RuntimeError("Native Codex checker rejected its metadata request")
                    return message["result"]
            raise RuntimeError("Native Codex metadata response timed out")

        try:
            send(
                {
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "racp_acceptance",
                            "title": "RACP MCP startup check",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                }
            )
            response(0)
            send({"method": "initialized", "params": {}})
            send(
                {
                    "id": 1,
                    "method": "mcpServerStatus/list",
                    "params": {"detail": "toolsAndAuthOnly", "limit": 100},
                }
            )
            servers = response(1)
            selected = next((item for item in servers["data"] if item["name"] == "racp"), None)
            if selected is None:
                raise RuntimeError("RACP was absent from the native Codex MCP catalog")
            names = sorted(selected.get("tools", {}))
            report = {
                "scope": "separate native Codex app-server startup; no thread or model turn",
                "server": "racp",
                "auth_status": selected.get("authStatus"),
                "initialized": selected.get("serverCapabilities") is not None,
                "tool_count": len(names),
                "tools": names,
            }
            report["status"] = (
                "PASS" if report["initialized"] and "device_list" in names else "FAIL"
            )
            (root / "dist/codex-mcp-native-startup-20261006.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            print(json.dumps({key: value for key, value in report.items() if key != "tools"}))
            if report["status"] != "PASS":
                raise RuntimeError("Native Codex could not initialize the RACP MCP tools")
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                import psutil

                owned = psutil.Process(process.pid)
                descendants = owned.children(recursive=True)
                for child in reversed(descendants):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
