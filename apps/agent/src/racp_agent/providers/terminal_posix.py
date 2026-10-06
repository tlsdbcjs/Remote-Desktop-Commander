import errno
import fcntl
import os
import pty
import struct
import subprocess
import termios
from typing import Any

from racp_agent.providers.containment import kill_group

unix_os: Any = os
unix_pty: Any = pty
unix_ioctl: Any = fcntl
unix_termios: Any = termios


class PosixTerminal:
    def __init__(
        self,
        argv: list[str],
        cwd: str,
        env: dict[str, str],
        cols: int,
        rows: int,
    ) -> None:
        self.fd, slave = unix_pty.openpty()
        try:
            self.resize(cols, rows)

            def child_setup() -> None:
                unix_os.setsid()
                unix_ioctl.ioctl(0, unix_termios.TIOCSCTTY, 0)

            self.process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                preexec_fn=child_setup,
                close_fds=True,
            )
            self.pid = self.process.pid
            os.set_blocking(self.fd, False)
        except BaseException:
            os.close(self.fd)
            raise
        finally:
            os.close(slave)

    def resize(self, cols: int, rows: int) -> None:
        unix_ioctl.ioctl(self.fd, unix_termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def read(self) -> bytes | None:
        try:
            return os.read(self.fd, 65536)
        except BlockingIOError:
            return None
        except OSError as exc:
            if exc.errno == errno.EIO:
                return b""
            raise

    def write(self, data: bytes) -> int:
        return os.write(self.fd, data)

    def poll(self) -> int | None:
        return self.process.poll()

    def terminate(self) -> None:
        try:
            kill_group(self.pid)
        except ProcessLookupError:
            pass

    def finish(self) -> None:
        self.terminate()
        self.process.wait(timeout=5)

    def close(self) -> None:
        self.finish()
        os.close(self.fd)
