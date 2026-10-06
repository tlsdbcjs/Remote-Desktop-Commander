"""Locally approved named folders; task-local path selection with pinned roots."""

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path

from pydantic import Field, ValidationInfo, model_validator
from racp_domain.models import RACPError
from racp_protocol.models import StrictModel

from racp_agent.providers.paths import Parent, PathGuard, is_link


class WorkspaceSpec(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    path: Path

    @model_validator(mode="after")
    def approved_local_folder(self, info: ValidationInfo) -> "WorkspaceSpec":
        if self.id == "default":
            raise ValueError("default is reserved for the primary workspace")
        if not self.path.is_absolute() or str(self.path).startswith(("\\\\", "//")):
            raise ValueError("workspace must be an absolute local folder")
        if info.context and info.context.get("stored_settings_preview") is True:
            return self
        if any(is_link(p.lstat()) for p in [self.path, *self.path.parents]):
            raise ValueError("workspace cannot follow links or reparse points")
        PathGuard(self.path)
        return self


def workspace_argument(value: str) -> WorkspaceSpec:
    identifier, separator, path = value.partition("=")
    if not separator or not path:
        raise ValueError("use ID=PATH for an allowed workspace")
    return WorkspaceSpec(id=identifier, path=Path(os.path.abspath(path)))


class WorkspacePaths:
    def __init__(self, root: Path, additional: tuple[WorkspaceSpec, ...] = ()) -> None:
        if len(additional) > 15 or len({item.id for item in additional}) != len(additional):
            raise ValueError("configure up to 15 distinct additional workspaces")
        self.guards = {"default": PathGuard(root)}
        self.guards.update({item.id: PathGuard(item.path) for item in additional})
        self.active: ContextVar[tuple[str, ...]] = ContextVar(
            "workspace_scope", default=("default",)
        )

    def get(self, identifier: str) -> PathGuard:
        guard = self.guards.get(identifier)
        if guard is None:
            raise RACPError("PERMISSION_DENIED", "workspace is not locally approved", layer="agent")
        return guard

    def inventory(self) -> list[dict[str, str]]:
        return [{"id": key, "path": str(value.root)} for key, value in self.guards.items()]

    @property
    def root(self) -> Path:
        return self.get(self.active.get()[0]).root

    @contextmanager
    def select(self, identifier: str, destination: str | None = None) -> Iterator[None]:
        ids = tuple(dict.fromkeys([identifier, destination or identifier]))
        for key in ids:
            self.get(key)
        marker = self.active.set(ids)
        try:
            yield
        finally:
            self.active.reset(marker)

    def path(self, raw: str) -> Path:
        return self.get(self.active.get()[0]).path(raw)

    def destination_path(self, raw: str, identifier: str | None) -> Path:
        return self.get(identifier or self.active.get()[0]).path(raw)

    def is_root(self, target: Path) -> bool:
        return any(target == guard.root for guard in self.guards.values())

    def root_for(self, target: Path) -> Path:
        roots = [
            self.get(key).root
            for key in self.active.get()
            if target.is_relative_to(self.get(key).root)
        ]
        if not roots:
            raise RACPError(
                "PATH_ACCESS_DENIED", "path is outside selected workspaces", layer="provider"
            )
        return max(roots, key=lambda root: len(root.parts))

    @contextmanager
    def directory(self, directory: Path) -> Iterator[Parent]:
        guards = [
            self.get(key)
            for key in self.active.get()
            if directory.is_relative_to(self.get(key).root)
        ]
        if not guards:
            raise RACPError(
                "PATH_ACCESS_DENIED", "directory is outside selected workspaces", layer="provider"
            )
        # Nested roots keep both configured identities pinned during cross-folder work.
        with ExitStack() as handles:
            parents = [handles.enter_context(guard.directory(directory)) for guard in guards]
            yield parents[-1]

    @contextmanager
    def parent(self, target: Path) -> Iterator[Parent]:
        if self.is_root(target):
            raise RACPError(
                "PATH_ACCESS_DENIED", "configured workspace roots are protected", layer="provider"
            )
        with self.directory(target.parent) as parent:
            yield parent

    def cwd(self, raw: str | None, identifier: str) -> Path:
        guard = self.get(identifier)
        path = guard.path(raw or ".")
        with guard.directory(path):
            return path
