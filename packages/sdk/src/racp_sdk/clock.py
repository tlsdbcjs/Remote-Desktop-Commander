import os
from pathlib import Path

import psutil


def clock_identity() -> str:
    if os.name != "nt":
        path = Path("/proc/sys/kernel/random/boot_id")
        if path.exists():
            return path.read_text().strip()
    # Only invalidate persisted monotonic readings; never derive a duration from UTC.
    return str(round(psutil.boot_time()))
