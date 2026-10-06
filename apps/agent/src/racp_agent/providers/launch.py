"""Windows containment gate. No target can start until parent assigns the Job Object."""

import json
import subprocess
import sys


def main() -> None:
    # Parent closes stdin after a bounded JSON config; target inherits EOF.
    args = json.loads(sys.stdin.buffer.readline(256 * 1024))
    if not isinstance(args, list) or not args or not all(isinstance(item, str) for item in args):
        raise ValueError("invalid launcher argv")
    result = subprocess.run(args, check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
