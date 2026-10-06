"""Stdlib-only Windows gate: target receives the protocol stdin after Job assignment."""

import json
import os
import subprocess


def main() -> None:
    bootstrap = bytearray()
    while len(bootstrap) <= 256 * 1024:
        byte = os.read(0, 1)  # No buffered read-ahead into the target's protocol frames.
        if byte == b"\n":
            break
        if not byte:
            raise ValueError("plugin gate permission missing")
        bootstrap.extend(byte)
    else:
        raise ValueError("plugin gate command exceeds limit")
    argv = json.loads(bootstrap)
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("invalid local plugin command")
    raise SystemExit(subprocess.call(argv))


if __name__ == "__main__":
    main()
