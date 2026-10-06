"""A registered process pinned by a kernel handle, not a user-supplied token or PID claim."""

import asyncio
from typing import Any


class PeerProcess:
    def __init__(self, pid: int, handle: Any) -> None:
        self.pid, self.handle = pid, handle
        self.closed = False

    @property
    def returncode(self) -> int | None:
        import win32event
        import win32process

        if self.closed:
            return 0
        if win32event.WaitForSingleObject(self.handle, 0) == win32event.WAIT_TIMEOUT:
            return None
        return int(win32process.GetExitCodeProcess(self.handle))

    def kill(self) -> None:
        import win32api

        if not self.closed and self.returncode is None:
            win32api.TerminateProcess(self.handle, 1)

    async def wait(self) -> int:
        import win32event

        while self.returncode is None:
            await asyncio.to_thread(win32event.WaitForSingleObject, self.handle, 100)
        return int(self.returncode or 0)

    def close(self) -> None:
        if not self.closed:
            self.handle.Close()
            self.closed = True
