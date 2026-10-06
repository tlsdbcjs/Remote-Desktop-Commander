"""Windows managed-process gate; stdlib only, run by the base interpreter."""

import json
import subprocess
import sys


def main() -> None:
    argv = json.loads(sys.stdin.buffer.readline(256 * 1024))
    try:
        child = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | (getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.argv[1:] == ["--hidden"] else 0),
        )
    except OSError:
        print(json.dumps({"error": "spawn_failed"}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps({"pid": child.pid}), flush=True)
    raise SystemExit(child.wait())


if __name__ == "__main__":
    main()
