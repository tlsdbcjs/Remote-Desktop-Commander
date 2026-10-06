import ctypes
import errno
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError


def windows_os_error(exc: Exception) -> OSError:
    if type(exc).__module__ != "pywintypes":
        raise exc
    code = int(exc.args[0])
    if code in {2, 3}:
        return FileNotFoundError(errno.ENOENT, "path was not found")
    if code in {80, 183}:
        return FileExistsError(errno.EEXIST, "path already exists")
    if code == 17:
        return OSError(errno.EXDEV, "cross-volume operation denied")
    return PermissionError(errno.EACCES, "Windows path access denied")


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def revision(info: os.stat_result) -> str:
    return f"{info.st_dev:x}-{info.st_ino:x}-{info.st_mtime_ns:x}-{info.st_size:x}"


class Parent:
    def __init__(self, directory: Path, fd: int | None) -> None:
        self.directory, self.fd = directory, fd

    def stat(self, name: str) -> os.stat_result:
        if self.fd is not None:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return os.lstat(self.directory / name)

    def open(self, name: str, flags: int, mode: int = 0o600) -> int:
        if self.fd is not None:
            return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=self.fd)
        import msvcrt

        import win32file

        access = 0x80000000  # GENERIC_READ
        if flags & (os.O_WRONLY | os.O_RDWR):
            access |= 0x40000000
        disposition = 1 if flags & os.O_EXCL else 4 if flags & os.O_CREAT else 3
        try:
            handle = win32file.CreateFile(
                str(self.directory / name),
                access,
                3,
                None,
                disposition,
                0x00200000 | 0x02000000,
                None,
            )
        except Exception as exc:
            raise windows_os_error(exc) from exc
        try:
            info = win32file.GetFileInformationByHandle(handle)
            if info[0] & 0x400:
                raise RACPError(
                    "PATH_ACCESS_DENIED", "reparse point access denied", layer="provider"
                )
            if info[0] & 0x10:
                raise RACPError("INVALID_ARGUMENT", "expected a regular file", layer="provider")
            open_handle = getattr(msvcrt, "open_osfhandle", None)
            if open_handle is None:
                raise RACPError(
                    "OPERATION_NOT_SUPPORTED",
                    "Windows file descriptor backend unavailable",
                    layer="provider",
                )
            fd = int(open_handle(int(handle.Detach()), flags | getattr(os, "O_BINARY", 0)))
            if flags & os.O_TRUNC:
                os.ftruncate(fd, 0)
            return fd
        finally:
            handle.Close()

    def unlink(self, name: str, *, directory: bool = False) -> None:
        if self.fd is not None:
            if directory:
                os.rmdir(name, dir_fd=self.fd)
            else:
                os.unlink(name, dir_fd=self.fd)
        elif directory:
            os.rmdir(self.directory / name)
        else:
            os.unlink(self.directory / name)

    def mkdir(self, name: str) -> None:
        if self.fd is not None:
            os.mkdir(name, mode=0o700, dir_fd=self.fd)
        else:
            os.mkdir(self.directory / name, mode=0o700)

    def replace(self, source: str, target: str) -> None:
        if self.fd is not None:
            os.replace(source, target, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self.directory / source, self.directory / target)

    def publish_create(self, source: str, target: str) -> None:
        if self.fd is not None:
            # link is an atomic no-overwrite publication, followed by removal of temp name.
            os.link(source, target, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
            os.unlink(source, dir_fd=self.fd)
        else:
            # Windows rename fails if target exists; os.replace would overwrite it.
            os.rename(self.directory / source, self.directory / target)

    def sync(self) -> None:
        if self.fd is not None:
            os.fsync(self.fd)

    def move_to(self, source: str, other: "Parent", destination: str, *, overwrite: bool) -> None:
        if self.fd is None:
            if overwrite:
                os.replace(self.directory / source, other.directory / destination)
            else:
                os.rename(self.directory / source, other.directory / destination)
        elif overwrite:
            os.replace(source, destination, src_dir_fd=self.fd, dst_dir_fd=other.fd)
        else:
            library = ctypes.CDLL(None, use_errno=True)
            rename = getattr(library, "renameat2", None)
            if rename is None:
                raise RACPError(
                    "OPERATION_NOT_SUPPORTED",
                    "atomic no-overwrite move is unavailable",
                    layer="provider",
                )
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            if rename(self.fd, os.fsencode(source), other.fd, os.fsencode(destination), 1):
                errno = ctypes.get_errno()
                raise OSError(errno, os.strerror(errno))


class PathGuard:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if os.name != "nt" and not hasattr(os, "O_NOFOLLOW"):
            raise ValueError("safe dirfd backend is unavailable on this OS")
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("workspace must be a directory")
        self.identity = info.st_dev, info.st_ino

    def path(self, raw: str) -> Path:
        if "\0" in raw or raw.startswith(("\\\\", "//", "~")):
            raise RACPError(
                "PATH_ACCESS_DENIED", "network, device, and expanded paths denied", layer="provider"
            )
        if os.name == "nt":
            drive, tail = os.path.splitdrive(raw)
            if drive and not tail.startswith(("/", "\\")) or ":" in tail:
                raise RACPError(
                    "PATH_ACCESS_DENIED", "drive-relative paths and ADS denied", layer="provider"
                )
            for part in Path(tail).parts:
                if part not in {"/", "\\", ".", ".."} and (
                    part.endswith((".", " "))
                    or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
                ):
                    raise RACPError(
                        "PATH_ACCESS_DENIED", "reserved Windows path denied", layer="provider"
                    )
        elif re.match(r"^[A-Za-z]:", raw):
            raise RACPError("PATH_ACCESS_DENIED", "foreign drive path denied", layer="provider")
        target = Path(raw)
        if not target.is_absolute():
            target = self.root / target
        target = Path(os.path.abspath(target))
        if not target.is_relative_to(self.root):
            raise RACPError("PATH_ACCESS_DENIED", "path is outside workspace", layer="provider")
        return target

    @contextmanager
    def parent(self, target: Path) -> Iterator[Parent]:
        if not target.is_relative_to(self.root) or target == self.root:
            raise RACPError("PATH_ACCESS_DENIED", "workspace root is protected", layer="provider")
        with self.directory(target.parent) as parent:
            yield parent

    @contextmanager
    def directory(self, directory: Path) -> Iterator[Parent]:
        if not directory.is_relative_to(self.root):
            raise RACPError(
                "PATH_ACCESS_DENIED", "directory is outside workspace", layer="provider"
            )
        handles: list[Any] = []
        descriptors: list[int] = []
        try:
            if os.name == "nt":
                import win32file

                # Lock every ancestor, including those above root, against delete/rename.
                for component in [*reversed(directory.parents), directory]:
                    try:
                        handle = win32file.CreateFile(
                            str(component), 0x80000000, 3, None, 3, 0x02000000 | 0x00200000, None
                        )
                    except Exception as exc:
                        raise windows_os_error(exc) from exc
                    handles.append(handle)
                    info = win32file.GetFileInformationByHandle(handle)
                    if info[0] & 0x400 or not info[0] & 0x10:
                        raise RACPError(
                            "PATH_ACCESS_DENIED",
                            "directory link/reparse point denied",
                            layer="provider",
                        )
                root_info = self.root.stat()
                if (root_info.st_dev, root_info.st_ino) != self.identity:
                    raise RACPError(
                        "PATH_ACCESS_DENIED", "workspace identity changed", layer="provider"
                    )
                yield Parent(directory, None)
            else:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(self.root, flags)
                descriptors.append(fd)
                root_info = os.fstat(fd)
                if (root_info.st_dev, root_info.st_ino) != self.identity:
                    raise RACPError(
                        "PATH_ACCESS_DENIED", "workspace identity changed", layer="provider"
                    )
                for part in directory.relative_to(self.root).parts:
                    fd = os.open(part, flags, dir_fd=fd)
                    descriptors.append(fd)
                yield Parent(directory, fd)
        except OSError as exc:
            raise RACPError(
                "PATH_NOT_FOUND" if isinstance(exc, FileNotFoundError) else "PATH_ACCESS_DENIED",
                "path could not be opened safely",
                layer="provider",
            ) from exc
        finally:
            for handle in reversed(handles):
                handle.Close()
            for fd in reversed(descriptors):
                os.close(fd)
