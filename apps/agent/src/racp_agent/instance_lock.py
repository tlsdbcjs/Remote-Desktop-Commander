"""OS-released exclusive lifetime locks; lock files are never unlinked."""

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Self

from racp_agent.providers.paths import PathGuard
from racp_agent.settings import local_path


class InstanceRunningError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = local_path(path.absolute())
        self.fd: int | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        guard = PathGuard(local_path(self.path.parent))
        self.path = guard.path(self.path.name)
        with guard.parent(self.path) as parent:
            descriptor = parent.open(self.path.name, os.O_RDWR | os.O_CREAT)
        try:
            os.set_inheritable(descriptor, False)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise InstanceRunningError("Another Agent or controller holds this state") from exc
            self.fd = descriptor
            return self
        except BaseException:
            os.close(descriptor)
            raise

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
