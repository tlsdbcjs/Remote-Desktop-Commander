import asyncio
import codecs
import errno
import hashlib
import json
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from racp_agent.providers.paths import Parent, PathGuard, is_link, revision, windows_os_error
from racp_agent.providers.text_files import patch_batch, search_content
from racp_agent.workspaces import WorkspacePaths, WorkspaceSpec
from racp_domain.models import ExecutionContext, RACPError
from racp_sdk.pagination import CursorCodec


@dataclass
class Budget:
    deadline: float
    cancelled: threading.Event
    gate: Callable[[], None] | None = None

    def check(self) -> None:
        if self.gate is not None:
            self.gate()
        if self.cancelled.is_set():
            raise RACPError("CANCELLED", "file operation cancelled", layer="provider")
        if time.monotonic() >= self.deadline:
            raise RACPError("TIMEOUT", "file operation timed out", layer="provider")


def metadata(path: Path, info: os.stat_result) -> dict[str, Any]:
    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": info.st_size,
        "revision": revision(info),
        "mtime_ns": str(info.st_mtime_ns),
        "is_link": is_link(info),
        "type": "link" if is_link(info) else "directory" if stat.S_ISDIR(info.st_mode) else "file",
    }


class FilesystemProvider:
    def __init__(self, root: Path, spool: Path, additional: tuple[WorkspaceSpec, ...] = ()) -> None:
        self.guard = WorkspacePaths(root, additional)
        if os.name == "nt":
            self.protected = [
                Path(os.environ.get("SYSTEMROOT", r"C:\Windows")),
                Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
            ]
        else:
            self.protected = [
                Path(path)
                for path in ("/proc", "/sys", "/dev", "/etc", "/usr", "/bin", "/sbin", "/boot")
            ]
        self.spool = spool
        self.cursor = CursorCodec()
        self.lock = threading.RLock()
        spool.mkdir(parents=True, exist_ok=True)
        self.cache_guard = PathGuard(spool)

    @staticmethod
    def info(parent: Parent, name: str, *, missing: bool = False) -> os.stat_result | None:
        try:
            info = parent.stat(name)
        except FileNotFoundError:
            if missing:
                return None
            raise RACPError("PATH_NOT_FOUND", "file was not found", layer="provider") from None
        if is_link(info):
            raise RACPError("PATH_ACCESS_DENIED", "link/reparse point denied", layer="provider")
        return info

    @staticmethod
    def file_hash(parent: Parent, name: str, budget: Budget) -> str:
        hasher = hashlib.sha256()
        with os.fdopen(parent.open(name, os.O_RDONLY), "rb") as stream:
            while chunk := stream.read(65536):
                budget.check()
                hasher.update(chunk)
        return hasher.hexdigest()

    def precondition(
        self, parent: Parent, name: str, payload: dict[str, Any], budget: Budget
    ) -> None:
        info = self.info(parent, name, missing=True)
        if payload.get("expected_revision") and (
            info is None or revision(info) != payload["expected_revision"]
        ):
            raise RACPError("PRECONDITION_FAILED", "file revision changed", layer="provider")
        if payload.get("expected_sha256") and (
            info is None or self.file_hash(parent, name, budget) != payload["expected_sha256"]
        ):
            raise RACPError("PRECONDITION_FAILED", "file content changed", layer="provider")

    def write(self, target: Path, payload: dict[str, Any], budget: Budget) -> dict[str, Any]:
        with self.guard.parent(target) as parent:
            original = self.info(parent, target.name, missing=True)
            self.precondition(parent, target.name, payload, budget)
            mode = payload["mode"]
            if mode == "create" and original:
                raise RACPError("CONFLICT", "file already exists", layer="provider")
            if mode in {"replace", "append"} and original is None:
                raise RACPError("PATH_NOT_FOUND", "file was not found", layer="provider")
            binary = payload.get("artifact_id") is not None
            if binary and not payload.get("_artifact_path"):
                raise RACPError(
                    "INVALID_ARGUMENT", "binary input was not downloaded", layer="provider"
                )
            content = payload["content"] or ""
            prefix = b""
            newline = payload["newline"]
            if not binary and original and mode in {"replace", "append"}:
                with os.fdopen(parent.open(target.name, os.O_RDONLY), "rb") as sample_stream:
                    sample = sample_stream.read(65536)
                if (
                    mode == "replace"
                    and sample.startswith(codecs.BOM_UTF8)
                    and payload["encoding"] == "utf-8"
                    and not content.startswith("\ufeff")
                ):
                    prefix = codecs.BOM_UTF8
                if newline == "preserve":
                    newline = "crlf" if b"\r\n" in sample else "lf"
            if newline in {"lf", "crlf"}:
                content = content.replace("\r\n", "\n")
                if newline == "crlf":
                    content = content.replace("\n", "\r\n")
            raw = prefix + content.encode(payload["encoding"])
            written_bytes = 0

            def blocks() -> Iterator[bytes]:
                if not binary:
                    budget.check()
                    yield raw
                    return
                cached = Path(payload["_artifact_path"])
                total = 0
                hasher = hashlib.sha256()
                with (
                    self.cache_guard.parent(cached) as cache_parent,
                    os.fdopen(cache_parent.open(cached.name, os.O_RDONLY), "rb") as source,
                ):
                    while block := source.read(65536):
                        budget.check()
                        total += len(block)
                        if total > payload["_artifact_size"]:
                            raise RACPError(
                                "CHECKSUM_MISMATCH",
                                "binary input exceeds Artifact size",
                                layer="provider",
                            )
                        hasher.update(block)
                        yield block
                if (
                    total != payload["_artifact_size"]
                    or hasher.hexdigest() != payload["_artifact_sha256"]
                ):
                    raise RACPError(
                        "CHECKSUM_MISMATCH",
                        "binary input changed before publication",
                        layer="provider",
                    )

            if mode == "append":
                fd = parent.open(target.name, os.O_RDWR | os.O_APPEND)
                with os.fdopen(fd, "ab") as stream:
                    if os.fstat(stream.fileno()).st_size != int(payload["expected_offset"]):
                        raise RACPError(
                            "PRECONDITION_FAILED", "append offset changed", layer="provider"
                        )
                    try:
                        for block in blocks():
                            stream.write(block)
                            stream.flush()
                            written_bytes += len(block)
                        os.fsync(stream.fileno())
                    except (OSError, RACPError) as exc:
                        accepted = max(
                            0, os.fstat(stream.fileno()).st_size - int(payload["expected_offset"])
                        )
                        code = (
                            exc.error.code if isinstance(exc, RACPError) else "RESOURCE_EXHAUSTED"
                        )
                        raise RACPError(
                            code,
                            "append did not complete",
                            layer="provider",
                            execution_state="completed",
                            accepted_bytes=accepted,
                            atomic=False,
                            cleanup_status="partial" if accepted else "complete",
                        ) from exc
                return {
                    **metadata(target, parent.stat(target.name)),
                    "written_bytes": written_bytes,
                    "atomic": False,
                    "external_writer_atomic_cas": False,
                }
            temporary = ".racp-" + secrets.token_hex(16) + ".tmp"
            try:
                with os.fdopen(
                    parent.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
                ) as stream:
                    for block in blocks():
                        stream.write(block)
                        written_bytes += len(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                if original:
                    if os.name == "nt":
                        import win32security

                        security = win32security.GetFileSecurity(str(target), 4)
                        win32security.SetFileSecurity(
                            str(parent.directory / temporary), 4, security
                        )
                    else:
                        os.chmod(temporary, stat.S_IMODE(original.st_mode), dir_fd=parent.fd)
                self.precondition(parent, target.name, payload, budget)
                budget.check()
                if mode == "create":
                    parent.publish_create(temporary, target.name)
                else:
                    parent.replace(temporary, target.name)
                parent.sync()
                return {
                    **metadata(target, parent.stat(target.name)),
                    "written_bytes": written_bytes,
                    "atomic": True,
                    "external_writer_atomic_cas": False,
                }
            finally:
                try:
                    parent.unlink(temporary)
                except FileNotFoundError:
                    pass

    def read(
        self, target: Path, payload: dict[str, Any], budget: Budget, context: ExecutionContext
    ) -> dict[str, Any]:
        with self.guard.parent(target) as parent:
            info = self.info(parent, target.name)
            assert info is not None
            with os.fdopen(parent.open(target.name, os.O_RDONLY), "rb") as stream:
                initial = stream.read(payload["max_bytes"] + 1)
                if not payload["binary"] and len(initial) <= payload["max_bytes"]:
                    try:
                        text = initial.decode(payload["encoding"], errors="strict")
                    except (UnicodeDecodeError, LookupError) as exc:
                        raise RACPError(
                            "INVALID_ARGUMENT",
                            "text decoding failed; request binary Artifact",
                            layer="provider",
                            next_action="fs_read(binary=true)",
                        ) from exc
                    return {
                        **metadata(target, info),
                        "text": text,
                        "encoding": payload["encoding"],
                        "bom": initial.startswith(codecs.BOM_UTF8),
                        "artifact_id": None,
                    }
                spool = self.spool / (context.operation_id + ".binary")
                size = 0
                try:
                    with spool.open("xb") as output:
                        chunk = initial
                        while chunk:
                            budget.check()
                            size += len(chunk)
                            if size > 1024**3:
                                raise RACPError(
                                    "RESOURCE_EXHAUSTED",
                                    "file exceeds 1 GiB Artifact limit",
                                    layer="provider",
                                )
                            output.write(chunk)
                            chunk = stream.read(65536)
                        output.flush()
                        os.fsync(output.fileno())
                except BaseException:
                    spool.unlink(missing_ok=True)
                    raise
                return {
                    **metadata(target, info),
                    "text": None,
                    "artifact_id": None,
                    "artifact_media_type": "application/octet-stream",
                    "spool_path": str(spool),
                }

    def list_directory(
        self, target: Path, payload: dict[str, Any], budget: Budget
    ) -> dict[str, Any]:
        with self.guard.directory(target) as parent:
            info = os.fstat(parent.fd) if parent.fd is not None else target.stat()
            scope = str(target) + ":" + str(payload["limit"])
            offset = self.cursor.decode(payload["cursor"], scope, revision(info))
            names = sorted(os.listdir(parent.fd if parent.fd is not None else target))
            items = []
            for name in names[offset : offset + payload["limit"]]:
                budget.check()
                try:
                    items.append(metadata(target / name, parent.stat(name)))
                except FileNotFoundError:
                    continue
            end = offset + payload["limit"]
            return {
                "items": items,
                "next_cursor": self.cursor.encode(end, scope, revision(info))
                if end < len(names)
                else None,
                "consistency": "best_effort",
            }

    def mkdir(self, target: Path, payload: dict[str, Any], budget: Budget) -> dict[str, Any]:
        paths = [target]
        if payload["parents"]:
            root = self.guard.root_for(target)
            paths = [
                root.joinpath(*target.relative_to(root).parts[:index])
                for index in range(1, len(target.relative_to(root).parts) + 1)
            ]
        for path in paths:
            budget.check()
            with self.guard.parent(path) as parent:
                existing = self.info(parent, path.name, missing=True)
                if existing:
                    if (
                        not stat.S_ISDIR(existing.st_mode)
                        or path == target
                        and not payload["exist_ok"]
                    ):
                        raise RACPError("CONFLICT", "path already exists", layer="provider")
                else:
                    parent.mkdir(path.name)
        return {"path": str(target), "created": True}

    def copy_file(
        self, source: Path, destination: Path, payload: dict[str, Any], budget: Budget
    ) -> dict[str, Any]:
        with self.guard.parent(source) as source_parent, self.guard.parent(destination) as parent:
            self.info(source_parent, source.name)
            original = self.info(parent, destination.name, missing=True)
            if original and not payload["overwrite"]:
                raise RACPError("CONFLICT", "destination already exists", layer="provider")
            if (
                payload.get("expected_sha256")
                and self.file_hash(source_parent, source.name, budget) != payload["expected_sha256"]
            ):
                raise RACPError("PRECONDITION_FAILED", "source content changed", layer="provider")
            temporary = ".racp-" + secrets.token_hex(16) + ".tmp"
            size = 0
            copied_hash = hashlib.sha256()
            try:
                with (
                    os.fdopen(source_parent.open(source.name, os.O_RDONLY), "rb") as reader,
                    os.fdopen(
                        parent.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
                    ) as writer,
                ):
                    while chunk := reader.read(65536):
                        budget.check()
                        writer.write(chunk)
                        copied_hash.update(chunk)
                        size += len(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if (
                    payload.get("expected_sha256")
                    and copied_hash.hexdigest() != payload["expected_sha256"]
                ):
                    raise RACPError(
                        "PRECONDITION_FAILED", "source changed while copying", layer="provider"
                    )
                budget.check()
                if payload["overwrite"]:
                    self.info(parent, destination.name, missing=True)
                    parent.replace(temporary, destination.name)
                else:
                    parent.publish_create(temporary, destination.name)
                parent.sync()
            finally:
                try:
                    parent.unlink(temporary)
                except FileNotFoundError:
                    pass
        return {
            "source": str(source),
            "destination": str(destination),
            "copied_bytes": size,
            "sha256": copied_hash.hexdigest(),
        }

    def walk(
        self, target: Path, budget: Budget, max_depth: int, *, complete: bool = False
    ) -> list[Path]:
        pending = [(target, 0)]
        result = []
        while pending:
            directory, depth = pending.pop()
            budget.check()
            with self.guard.directory(directory) as parent:
                for name in sorted(os.listdir(parent.fd if parent.fd is not None else directory)):
                    info = self.info(parent, name)
                    assert info is not None
                    path = directory / name
                    result.append(path)
                    if len(result) > 10000:
                        raise RACPError(
                            "RESOURCE_EXHAUSTED", "tree exceeds 10000 entries", layer="provider"
                        )
                    if stat.S_ISDIR(info.st_mode):
                        if depth < max_depth:
                            pending.append((path, depth + 1))
                        elif complete:
                            raise RACPError(
                                "RESOURCE_EXHAUSTED", "tree exceeds maximum depth", layer="provider"
                            )
        return result

    def partial_failure(
        self,
        context: ExecutionContext,
        action: str,
        completed: list[str],
        failed: Path,
        cause: Exception,
    ) -> None:
        report = self.spool / (context.operation_id + ".report.json")
        report.write_text(
            json.dumps({"completed": completed, "failed": [str(failed)]}), encoding="utf-8"
        )
        raise RACPError(
            cause.error.code if isinstance(cause, RACPError) else "INTERNAL_ERROR",
            action + " partially completed",
            layer="provider",
            execution_state="completed",
            report_spool_path=str(report),
            cleanup_status="partial",
        ) from cause

    def copy_tree(
        self,
        target: Path,
        destination: Path,
        payload: dict[str, Any],
        budget: Budget,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        if not payload["recursive"]:
            raise RACPError(
                "INVALID_ARGUMENT", "directory copy requires recursive=true", layer="provider"
            )
        if destination.is_relative_to(target) or target.is_relative_to(destination):
            raise RACPError(
                "INVALID_ARGUMENT", "source and destination trees overlap", layer="provider"
            )
        paths = self.walk(target, budget, 20, complete=True)
        self.mkdir(destination, {"parents": False, "exist_ok": payload["overwrite"]}, budget)
        completed = [str(destination)]
        manifest: dict[str, str | None] = {}
        for path in sorted(paths, key=lambda item: len(item.parts)):
            copied_target = destination / path.relative_to(target)
            info: os.stat_result | None
            try:
                if self.guard.is_root(path):
                    with self.guard.directory(path) as parent:
                        info = os.fstat(parent.fd) if parent.fd is not None else path.stat()
                else:
                    with self.guard.parent(path) as parent:
                        info = self.info(parent, path.name)
                        assert info is not None
                if stat.S_ISDIR(info.st_mode):
                    self.mkdir(
                        copied_target, {"parents": False, "exist_ok": payload["overwrite"]}, budget
                    )
                    manifest[str(path)] = None
                else:
                    copied = self.copy_file(path, copied_target, payload, budget)
                    manifest[str(path)] = copied["sha256"]
                completed.append(str(copied_target))
            except (OSError, RACPError) as exc:
                self.partial_failure(context, "recursive copy", completed, copied_target, exc)
        result = {
            "source": str(target),
            "destination": str(destination),
            "completed_count": len(completed),
        }
        if payload.get("copy_and_delete"):
            result["source_manifest"] = manifest
        return result

    def _execute(
        self,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
        cancelled: threading.Event,
        gate: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        budget = Budget(time.monotonic() + context.timeout_ms / 1000, cancelled, gate)
        budget.check()
        if operation == "filesystem.patch":
            with self.lock:
                return patch_batch(self, payload, budget)
        target = self.guard.path(payload.get("path", payload.get("source", ".")))
        if operation in {"filesystem.delete", "filesystem.move"} and any(
            guard.root.is_relative_to(target) for guard in self.guard.guards.values()
        ):
            raise RACPError(
                "PATH_ACCESS_DENIED",
                "operation would remove a configured workspace",
                layer="provider",
            )
        if operation in {
            "filesystem.delete",
            "filesystem.move",
            "filesystem.write",
            "filesystem.mkdir",
        } and any(
            target == protected or target.is_relative_to(protected) for protected in self.protected
        ):
            raise RACPError("PATH_ACCESS_DENIED", "system path is protected", layer="provider")
        if operation in {"filesystem.copy", "filesystem.move"}:
            destination = self.guard.destination_path(
                payload["destination"], payload.get("destination_workspace_id")
            )
            if any(
                destination == protected or destination.is_relative_to(protected)
                for protected in self.protected
            ):
                raise RACPError(
                    "PATH_ACCESS_DENIED", "destination is a protected system path", layer="provider"
                )
        if operation == "filesystem.read":
            return self.read(target, payload, budget, context)
        if operation == "filesystem.list":
            return self.list_directory(target, payload, budget)
        if operation == "filesystem.stat":
            if self.guard.is_root(target):
                with self.guard.directory(target) as parent:
                    return metadata(
                        target, os.fstat(parent.fd) if parent.fd is not None else target.stat()
                    )
            with self.guard.parent(target) as parent:
                # stat reports link itself, but never follows it.
                return metadata(target, parent.stat(target.name))
        if operation == "filesystem.hash":
            with self.guard.parent(target) as parent:
                self.info(parent, target.name)
                return {
                    "path": str(target),
                    "algorithm": "sha256",
                    "sha256": self.file_hash(parent, target.name, budget),
                }
        if operation == "filesystem.search":
            paths = self.walk(target, budget, payload["max_depth"])
            needle = (
                payload["pattern"] if payload["case_sensitive"] else payload["pattern"].casefold()
            )
            matched = [
                str(path)
                for path in paths
                if needle in (path.name if payload["case_sensitive"] else path.name.casefold())
            ]
            return {
                "items": matched[: payload["max_results"]],
                "truncated": len(matched) > payload["max_results"],
                "max_depth": payload["max_depth"],
                "max_results": payload["max_results"],
                "mode": "literal",
            }
        if operation == "filesystem.search_content":
            return search_content(self, target, payload, budget)
        with self.lock:
            if operation == "filesystem.write":
                return self.write(target, payload, budget)
            if operation == "filesystem.mkdir":
                return self.mkdir(target, payload, budget)
            if operation == "filesystem.copy":
                source_info: os.stat_result | None
                destination = self.guard.destination_path(
                    payload["destination"], payload.get("destination_workspace_id")
                )
                if self.guard.is_root(target):
                    with self.guard.directory(target) as directory:
                        source_info = (
                            os.fstat(directory.fd) if directory.fd is not None else target.stat()
                        )
                else:
                    with self.guard.parent(target) as source_parent:
                        source_info = self.info(source_parent, target.name)
                        assert source_info is not None
                if stat.S_ISDIR(source_info.st_mode):
                    return self.copy_tree(target, destination, payload, budget, context)
                return self.copy_file(target, destination, payload, budget)
            if operation == "filesystem.move":
                destination = self.guard.destination_path(
                    payload["destination"], payload.get("destination_workspace_id")
                )
                with self.guard.parent(target) as source, self.guard.parent(destination) as dest:
                    source_info = self.info(source, target.name)
                    assert source_info is not None
                    self.precondition(source, target.name, payload, budget)
                    if self.info(dest, destination.name, missing=True) and not payload["overwrite"]:
                        raise RACPError("CONFLICT", "destination already exists", layer="provider")
                    budget.check()
                    destination_device = (
                        os.fstat(dest.fd).st_dev
                        if dest.fd is not None
                        else dest.directory.stat().st_dev
                    )
                    if source_info.st_dev != destination_device:
                        if not payload["copy_and_delete"]:
                            raise RACPError(
                                "CONFLICT",
                                "cross-volume move requires explicit copy-and-delete Job",
                                layer="provider",
                            )
                        if stat.S_ISDIR(source_info.st_mode):
                            copied = self.copy_tree(target, destination, payload, budget, context)
                            manifest = copied.pop("source_manifest")
                            remaining = self.walk(target, budget, 20, complete=True)
                            if set(map(str, remaining)) != set(manifest):
                                self.partial_failure(
                                    context,
                                    "cross-volume move",
                                    [str(destination)],
                                    target,
                                    RACPError(
                                        "PRECONDITION_FAILED",
                                        "source tree changed while copying",
                                        layer="provider",
                                    ),
                                )
                            for path in sorted(
                                remaining, key=lambda item: len(item.parts), reverse=True
                            ):
                                try:
                                    budget.check()
                                    with self.guard.parent(path) as original_parent:
                                        info = self.info(original_parent, path.name)
                                        assert info is not None
                                        if manifest[str(path)] is not None:
                                            self.precondition(
                                                original_parent,
                                                path.name,
                                                {"expected_sha256": manifest[str(path)]},
                                                budget,
                                            )
                                        original_parent.unlink(
                                            path.name, directory=stat.S_ISDIR(info.st_mode)
                                        )
                                except (OSError, RACPError) as exc:
                                    self.partial_failure(
                                        context, "cross-volume move", [str(destination)], path, exc
                                    )
                            source.unlink(target.name, directory=True)
                        else:
                            copied = self.copy_file(target, destination, payload, budget)
                            try:
                                self.precondition(
                                    source,
                                    target.name,
                                    {"expected_sha256": copied["sha256"]},
                                    budget,
                                )
                                budget.check()
                                source.unlink(target.name)
                            except (OSError, RACPError) as exc:
                                self.partial_failure(
                                    context, "cross-volume move", [str(destination)], target, exc
                                )
                        source.sync()
                        return {
                            "source": str(target),
                            "destination": str(destination),
                            "atomic": False,
                            "external_writer_atomic_cas": False,
                        }
                    source.move_to(
                        target.name, dest, destination.name, overwrite=payload["overwrite"]
                    )
                    return {"source": str(target), "destination": str(destination), "atomic": True}
            if operation == "filesystem.delete":
                with self.guard.parent(target) as parent:
                    info = self.info(parent, target.name)
                    assert info is not None
                    self.precondition(parent, target.name, payload, budget)
                    if stat.S_ISDIR(info.st_mode):
                        if not payload["recursive"]:
                            parent.unlink(target.name, directory=True)
                            return {"path": str(target), "deleted": True}
                        if payload["expected_revision"] is None:
                            raise RACPError(
                                "INVALID_ARGUMENT",
                                "recursive delete requires expected_revision",
                                layer="provider",
                            )
                        paths = self.walk(target, budget, 20, complete=True)
                        completed = []
                        for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
                            try:
                                budget.check()
                                with self.guard.parent(path) as child_parent:
                                    child_info = self.info(child_parent, path.name)
                                    assert child_info is not None
                                    child_parent.unlink(
                                        path.name, directory=stat.S_ISDIR(child_info.st_mode)
                                    )
                                completed.append(str(path))
                            except (OSError, RACPError) as exc:
                                self.partial_failure(
                                    context, "recursive delete", completed, path, exc
                                )
                        parent.unlink(target.name, directory=True)
                    else:
                        budget.check()
                        parent.unlink(target.name)
                    return {"path": str(target), "deleted": True}
        raise RACPError(
            "OPERATION_NOT_SUPPORTED", "filesystem operation not implemented", layer="provider"
        )

    def selected_execute(
        self,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
        cancelled: threading.Event,
        gate: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        with self.guard.select(context.workspace_id, payload.get("destination_workspace_id")):
            return self._execute(operation, payload, context, cancelled, gate)

    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext,
        *, gate: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        cancelled = threading.Event()
        work = asyncio.create_task(
            asyncio.to_thread(self.selected_execute, operation, payload, context, cancelled, gate)
        )
        try:
            try:
                result = await asyncio.shield(work)
            except asyncio.CancelledError:
                cancelled.set()
                result = await asyncio.shield(work)
            if len(json.dumps(result).encode()) > 65536:
                spool = self.spool / (context.operation_id + ".json")
                await asyncio.to_thread(
                    spool.write_text, json.dumps(result, ensure_ascii=False), encoding="utf-8"
                )
                result = {
                    "artifact_id": None,
                    "artifact_media_type": "application/json",
                    "spool_path": str(spool),
                    "truncated": True,
                }
            return {"state": "SUCCEEDED", "result": result, "error": None}
        except OSError as exc:
            code = (
                "CONFLICT"
                if isinstance(exc, FileExistsError) or exc.errno == errno.EXDEV
                else "PATH_NOT_FOUND"
                if isinstance(exc, FileNotFoundError)
                else "PATH_ACCESS_DENIED"
            )
            raise RACPError(code, "filesystem operation failed", layer="provider") from exc
        except Exception as exc:
            if type(exc).__module__ != "pywintypes":
                raise
            mapped = windows_os_error(exc)
            raise RACPError(
                "PATH_ACCESS_DENIED", "Windows file operation denied", layer="provider"
            ) from mapped
