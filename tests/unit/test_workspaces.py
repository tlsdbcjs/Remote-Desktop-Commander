import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from racp_agent.providers.filesystem import FilesystemProvider
from racp_agent.providers.shell import ShellProvider
from racp_agent.workspaces import WorkspacePaths, WorkspaceSpec
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.registry import validate_payload


def context(workspace: str = "default") -> ExecutionContext:
    return ExecutionContext(
        "op_test", "req_test", "0" * 32, "dev_test", "owner_local", "boot_test", 30000, workspace
    )


def folders(tmp_path: Path) -> tuple[Path, Path, tuple[WorkspaceSpec, ...]]:
    first, second = tmp_path / "first", tmp_path / "자료"
    first.mkdir()
    second.mkdir()
    return first, second, (WorkspaceSpec(id="docs", path=second),)


async def test_parallel_requests_keep_their_selected_roots(tmp_path: Path) -> None:
    first, second, additional = folders(tmp_path)
    (first / "same.txt").write_text("first")
    (second / "same.txt").write_text("second")
    provider = FilesystemProvider(first, tmp_path / "spool", additional)
    values = await asyncio.gather(
        *[
            provider.execute(
                "filesystem.read",
                validate_payload("filesystem.read", {"path": "same.txt"}),
                context("docs" if index % 2 else "default"),
            )
            for index in range(40)
        ]
    )
    assert [v["result"]["text"] for v in values] == [
        "second" if i % 2 else "first" for i in range(40)
    ]


async def test_cross_folder_copy_move_and_protected_roots(tmp_path: Path) -> None:
    first, second, additional = folders(tmp_path)
    (first / "tree").mkdir()
    (first / "tree/note.txt").write_text("copy me")
    provider = FilesystemProvider(first, tmp_path / "spool", additional)
    copied = await provider.execute(
        "filesystem.copy",
        validate_payload(
            "filesystem.copy",
            {
                "source": "tree",
                "destination": "copied",
                "recursive": True,
                "destination_workspace_id": "docs",
            },
        ),
        context(),
    )
    assert copied["state"] == "SUCCEEDED" and (second / "copied/note.txt").read_text() == "copy me"
    await provider.execute(
        "filesystem.move",
        validate_payload(
            "filesystem.move",
            {
                "source": "copied/note.txt",
                "destination": "returned.txt",
                "destination_workspace_id": "default",
            },
        ),
        context("docs"),
    )
    assert (first / "returned.txt").read_text() == "copy me" and not (
        second / "copied/note.txt"
    ).exists()
    for identifier in ["default", "docs"]:
        with pytest.raises(RACPError):
            await provider.execute(
                "filesystem.delete",
                validate_payload("filesystem.delete", {"path": ".", "recursive": True}),
                context(identifier),
            )
    assert first.is_dir() and second.is_dir()


async def test_unknown_workspace_and_cross_root_escape_are_denied(tmp_path: Path) -> None:
    first, second, additional = folders(tmp_path)
    (second / "secret.txt").write_text("allowed only with docs selected")
    provider = FilesystemProvider(first, tmp_path / "spool", additional)
    for identifier, path in [
        ("unknown", "."),
        ("default", str(second / "secret.txt")),
        ("default", "../資料/secret.txt"),
    ]:
        with pytest.raises(RACPError):
            await provider.execute(
                "filesystem.read",
                validate_payload("filesystem.read", {"path": path}),
                context(identifier),
            )
    with pytest.raises(RACPError):
        await provider.execute(
            "filesystem.copy",
            validate_payload(
                "filesystem.copy",
                {"source": "none", "destination": "nope", "destination_workspace_id": "unknown"},
            ),
            context(),
        )
    assert not (second / "nope").exists()


def test_shell_directory_selection_and_escape_checks(tmp_path: Path) -> None:
    first, second, additional = folders(tmp_path)
    shell = ShellProvider(first, tmp_path / "spool", additional)
    assert shell.paths.cwd(None, "docs") == second.resolve()
    assert shell.paths.cwd(".", "default") == first.resolve()
    for path in [str(second), ".."]:
        with pytest.raises(RACPError):
            shell.paths.cwd(path, "default")
    with pytest.raises(RACPError):
        shell.paths.cwd(None, "missing")


async def test_nested_workspace_cross_copy_keeps_both_roots_pinned(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "a.txt").write_text("nested")
    provider = FilesystemProvider(
        root, tmp_path / "spool", (WorkspaceSpec(id="nested", path=nested),)
    )
    await provider.execute(
        "filesystem.copy",
        validate_payload(
            "filesystem.copy",
            {
                "source": "a.txt",
                "destination": "b.txt",
                "destination_workspace_id": "nested",
            },
        ),
        context(),
    )
    assert (nested / "b.txt").read_text() == "nested"
    with pytest.raises(RACPError):
        await provider.execute(
            "filesystem.delete",
            validate_payload("filesystem.delete", {"path": "nested", "recursive": True}),
            context(),
        )


async def test_copy_entire_root_and_protect_nested_root_ancestor_before_mutation(
    tmp_path: Path,
) -> None:
    first, second, additional = folders(tmp_path)
    parent = first / "parent"
    nested = parent / "nested"
    nested.mkdir(parents=True)
    (parent / "a.txt").write_text("keep")
    (nested / "b.txt").write_text("also keep")
    provider = FilesystemProvider(
        first, tmp_path / "spool", (*additional, WorkspaceSpec(id="nested", path=nested))
    )
    await provider.execute(
        "filesystem.copy",
        validate_payload(
            "filesystem.copy",
            {
                "source": ".",
                "destination": "backup",
                "recursive": True,
                "destination_workspace_id": "docs",
            },
        ),
        context(),
    )
    assert (second / "backup/parent/nested/b.txt").read_text() == "also keep"
    for operation, payload in [
        ("filesystem.delete", {"path": "parent", "recursive": True}),
        ("filesystem.move", {"source": "parent", "destination": "renamed"}),
    ]:
        with pytest.raises(RACPError):
            await provider.execute(operation, validate_payload(operation, payload), context())
        assert (parent / "a.txt").read_text() == "keep"
        assert (nested / "b.txt").read_text() == "also keep"


def test_local_workspace_configuration_rejects_invalid_names_paths_and_duplicate_ids(
    tmp_path: Path,
) -> None:
    first, second, additional = folders(tmp_path)
    for identifier, path in [
        ("default", second),
        ("../other", second),
        ("x", Path("relative")),
        ("x", tmp_path / "missing"),
    ]:
        with pytest.raises((ValueError, OSError)):
            WorkspaceSpec(id=identifier, path=path)
    with pytest.raises(ValueError):
        WorkspacePaths(first, additional * 2)


def test_configured_workspace_identity_change_and_junction_are_denied(tmp_path: Path) -> None:
    first, second, additional = folders(tmp_path)
    paths = WorkspacePaths(first, additional)
    backup = tmp_path / "previous"
    assert second.resolve().is_relative_to(tmp_path.resolve())
    assert backup.resolve().is_relative_to(tmp_path.resolve())
    second.rename(backup)
    second.mkdir()
    with pytest.raises(RACPError):
        paths.cwd(None, "docs")
    link = first / "escape"
    if os.name == "nt":
        result = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(link), str(backup)],
            capture_output=True,
        )
        assert result.returncode == 0
    else:
        link.symlink_to(backup, target_is_directory=True)
    try:
        with pytest.raises(RACPError):
            paths.cwd("escape", "default")
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()
