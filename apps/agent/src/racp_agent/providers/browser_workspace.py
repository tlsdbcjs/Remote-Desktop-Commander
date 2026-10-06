"""Private, explicitly owned browser files; no global TEMP directory cleanup."""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import psutil
from racp_agent.providers.paths import is_link
from racp_domain.models import RACPError


class BrowserWorkspace:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if is_link(root.lstat()):
            raise RACPError("PERMISSION_DENIED", "browser root cannot be a link", layer="provider")
        self.root = root.resolve(strict=True)
        if os.name == "nt":
            import win32api
            import win32con
            import win32file
            import win32security

            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
            )
            try:
                sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            finally:
                token.Close()
            acl = win32security.ACL()
            for principal in (
                sid,
                win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
            ):
                acl.AddAccessAllowedAceEx(
                    win32security.ACL_REVISION_DS,
                    win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE,
                    win32file.FILE_ALL_ACCESS,
                    principal,
                )
            win32security.SetNamedSecurityInfo(
                str(self.root),
                win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION
                | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                acl,
                None,
            )
        else:
            self.root.chmod(0o700)

    def checked(self, path: Path) -> Path:
        # Check the absolute final target before any recursive removal.
        if (
            path.resolve().parent != self.root
            or not re.fullmatch(r"browser_[a-f0-9]{32}", path.name)
            or path.exists()
            and is_link(path.lstat())
        ):
            raise RACPError(
                "PERMISSION_DENIED", "browser directory scope mismatch", layer="provider"
            )
        return path

    def create(self, id: str) -> Path:
        path = self.checked(self.root / id)
        if sum(1 for _ in self.root.iterdir()) >= 24:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "browser temporary history limit reached", layer="provider"
            )
        path.mkdir(mode=0o700)
        for child in ("temp", "downloads", "uploads"):
            (path / child).mkdir(mode=0o700)
        self.mark(path)
        return path

    def mark(self, path: Path, worker: int | None = None) -> None:
        self.checked(path)
        value = {
            "version": 1,
            "browser_id": path.name,
            "owner_pid": os.getpid(),
            "owner_birth": psutil.Process().create_time(),
            "worker_pid": worker,
            "worker_birth": psutil.Process(worker).create_time() if worker else None,
        }
        file = path / "ownership.json"
        with file.open("w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def alive(pid: int | None, birth: float | None) -> bool:
        if not pid or birth is None:
            return False
        try:
            return psutil.Process(pid).create_time() == birth
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True

    def remove(self, path: Path) -> None:
        path = self.checked(path)
        if path.exists():
            shutil.rmtree(path)  # Python 3.12 does not traverse Windows junction contents.

    def collect(self) -> dict[str, int]:
        result = {"removed": 0, "live": 0, "unverified": 0}
        for path in list(self.root.iterdir())[:24]:
            try:
                self.checked(path)
                marker = path / "ownership.json"
                if marker.stat().st_size > 4096:
                    raise ValueError("oversize ownership record")
                record: dict[str, Any] = json.loads(marker.read_text(encoding="utf-8"))
                if record.get("version") != 1 or record.get("browser_id") != path.name:
                    raise ValueError("ownership differs")
                if self.alive(record["owner_pid"], record["owner_birth"]) or self.alive(
                    record["worker_pid"], record["worker_birth"]
                ):
                    result["live"] += 1
                    continue
                if record.get("remote_cdp"):
                    result["unverified"] += 1
                    continue
                self.remove(path)
                result["removed"] += 1
            except (OSError, ValueError, KeyError, TypeError, RACPError):
                result["unverified"] += 1
        return result

    def usage(self, path: Path) -> int:
        self.checked(path)
        total, count = 0, 0
        pending = [path]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    count += 1
                    if count > 50000:
                        raise RACPError(
                            "RESOURCE_EXHAUSTED",
                            "browser file count limit reached",
                            layer="provider",
                        )
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if is_link(info):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        else:
                            total += info.st_size
                    except FileNotFoundError:
                        pass
        return total
