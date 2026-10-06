import os
import signal


def kill_group(pid: int) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        raise RuntimeError("process group cleanup is unavailable")
    killpg(pid, int(getattr(signal, "SIGKILL", 9)))
