"""Owned protocol fault fixture; not an RE implementation or an installable backend."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--health", default="okay")
    parser.add_argument("--descendant", action="store_true")
    args = parser.parse_args()
    if args.descendant:
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", "import time; time.sleep(120)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        (args.root / "descendants.json").write_text(
            json.dumps([os.getpid(), child.pid]), encoding="utf-8"
        )
        time.sleep(120)
        return
    assert args.manifest is not None
    for raw in sys.stdin.buffer:
        request = json.loads(raw)
        if request["type"] == "cancel":
            (args.root / "cancel-received").write_text("yes", encoding="utf-8")
            continue
        response = {
            "type": "result",
            "protocol_version": 1,
            "instance_id": request["instance_id"],
            "request_id": request["request_id"],
            "state": "SUCCEEDED",
            "result": {"value": request["payload"].get("value", 0)},
        }
        if request["operation"] == "plugin.health":
            if args.health == "hang":
                time.sleep(120)
            response["result"] = {
                "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                "backend_version": "1" if args.health == "okay" else "wrong",
            }
        else:
            action = request["payload"]["action"]
            if action == "crash":
                count = args.root / "side-effects"
                count.write_text(str(int(count.read_text()) + 1) if count.exists() else "1")
                os._exit(3)
            if action == "hang":
                subprocess.Popen(
                    [sys.executable, "-I", __file__, "--root", str(args.root), "--descendant"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                time.sleep(120)
            if action == "oversized":
                sys.stdout.buffer.write(b"x" * (1024 * 1024 + 10) + b"\n")
                sys.stdout.buffer.flush()
                continue
            if action == "invalid":
                response["result"] = {"value": "wrong-type"}
            if action == "wrong_id":
                response["request_id"] = "req_foreign"
            if action == "wrong_instance":
                response["instance_id"] = "provider_foreign"
            if action == "flood":
                for index in range(65):
                    print(
                        json.dumps(
                            {
                                "type": "event",
                                "protocol_version": 1,
                                "instance_id": request["instance_id"],
                                "sequence": str(index + 1),
                                "kind": "fixture.stop",
                                "data": {"reason": "owned-fixture"},
                            }
                        ),
                        flush=True,
                    )
                continue
            if action == "env":
                response["result"] = {
                    "value": int(
                        any(key.startswith(("OPENAI_", "AWS_", "RACP_")) for key in os.environ)
                    )
                }
            if action == "reject":
                response.pop("result")
                response.update(
                    state="FAILED",
                    error={
                        "code": "INVALID_ARGUMENT",
                        "message": "fixture rejected content",
                        "execution_state": "not_started",
                    },
                )
            if action == "stderr":
                sys.stderr.buffer.write(b"log" * 30000)
                sys.stderr.buffer.flush()
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
