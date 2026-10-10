"""Literal text search and exact text edits through the existing anchored file backend."""

import codecs
import difflib
import hashlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from racp_agent.providers.paths import is_link, revision
from racp_domain.models import RACPError

if TYPE_CHECKING:
    from racp_agent.providers.filesystem import Budget, FilesystemProvider


def read_text(
    provider: "FilesystemProvider", target: Path, limit: int, budget: "Budget"
) -> tuple[bytes, os.stat_result]:
    with provider.guard.parent(target) as parent:
        info = provider.info(parent, target.name)
        assert info is not None
        if not stat.S_ISREG(info.st_mode):
            raise RACPError(
                "INVALID_ARGUMENT", "Text target must be a regular file", layer="provider"
            )
        if info.st_size > limit:
            raise RACPError("RESOURCE_EXHAUSTED", "Text file exceeds byte budget", layer="provider")
        with os.fdopen(
            parent.open(target.name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb"
        ) as stream:
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode) or revision(initial) != revision(info):
                raise RACPError(
                    "PRECONDITION_FAILED", "Text file identity changed", layer="provider"
                )
            raw = bytearray()
            while len(raw) < initial.st_size:
                budget.check()
                block = stream.read(min(65536, initial.st_size - len(raw)))
                if not block:
                    break
                raw.extend(block)
            budget.check()
            if len(raw) != initial.st_size or revision(os.fstat(stream.fileno())) != revision(
                initial
            ):
                raise RACPError(
                    "PRECONDITION_FAILED", "Text file changed during observation", layer="provider"
                )
        return bytes(raw), initial


def search_content(
    provider: "FilesystemProvider", target: Path, payload: dict[str, Any], budget: "Budget"
) -> dict[str, Any]:
    scanned = 0
    files = 0
    entries = 0
    skipped: dict[str, int] = {}
    limits: set[str] = set()
    matches: list[dict[str, Any]] = []
    needle = payload["pattern"] if payload["case_sensitive"] else payload["pattern"].casefold()

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    def candidates() -> Iterator[Path]:
        nonlocal entries
        if not provider.guard.is_root(target):
            with provider.guard.parent(target) as parent:
                info = provider.info(parent, target.name)
                assert info is not None
                if not stat.S_ISDIR(info.st_mode):
                    yield target
                    return
        pending = [(target, 0)]
        while pending:
            directory, depth = pending.pop()
            budget.check()
            with provider.guard.directory(directory) as parent:
                with os.scandir(parent.fd if parent.fd is not None else directory) as listing:
                    for entry in listing:
                        budget.check()
                        entries += 1
                        if entries > 10000:
                            limits.add("entry_budget")
                            return
                        try:
                            info = parent.stat(entry.name)
                        except OSError:
                            skip("unavailable")
                            continue
                        if is_link(info):
                            skip("link_or_reparse")
                            continue
                        path = directory / entry.name
                        if stat.S_ISDIR(info.st_mode):
                            if depth < payload["max_depth"]:
                                pending.append((path, depth + 1))
                            else:
                                limits.add("max_depth")
                        elif stat.S_ISREG(info.st_mode):
                            yield path
                        else:
                            skip("non_regular")

    for path in candidates():
        budget.check()
        if files >= payload["max_files"]:
            limits.add("file_budget")
            break
        files += 1
        try:
            with provider.guard.parent(path) as parent:
                info = provider.info(parent, path.name)
                assert info is not None
                if info.st_size > payload["max_file_bytes"]:
                    skip("oversized")
                    continue
                if info.st_size > payload["max_scan_bytes"] - scanned:
                    limits.add("scan_byte_budget")
                    break
                # Reserve before the read: a failed changing-file observation also
                # consumes scan credit and cannot bypass the total byte ceiling.
                scanned += info.st_size
                raw, observed = read_text(provider, path, info.st_size, budget)
            try:
                text = raw.decode(payload["encoding"])
            except UnicodeError:
                skip("encoding")
                continue
            if "\x00" in text:
                skip("binary")
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                budget.check()
                haystack = line if payload["case_sensitive"] else line.casefold()
                column = haystack.find(needle)
                if column < 0:
                    continue
                if not payload["case_sensitive"]:
                    # casefold can expand a character (e.g. ß -> ss). Convert the
                    # match position back to the source's Unicode codepoint index.
                    folded = 0
                    for start in range(0, len(line), 512):
                        budget.check()
                        part = line[start : start + 512]
                        width = len(part.casefold())
                        if folded + width > column:
                            for index, char in enumerate(part):
                                char_width = len(char.casefold())
                                if folded + char_width > column:
                                    column = start + index
                                    break
                                folded += char_width
                            break
                        folded += width
                if len(matches) >= payload["max_results"]:
                    limits.add("result_budget")
                    break
                preview_start = max(0, column - 128)
                matches.append(
                    {
                        "path": str(path),
                        "line": line_number,
                        "column": column + 1,
                        "preview": line[preview_start : preview_start + 256],
                        "preview_start_column": preview_start + 1,
                        "preview_truncated": preview_start > 0 or len(line) > preview_start + 256,
                        "revision": revision(observed),
                    }
                )
            if "result_budget" in limits:
                break
        except RACPError as error:
            if error.error.code in {
                "CANCELLED",
                "TIMEOUT",
                "PERMISSION_DENIED",
                "APPROVAL_REQUIRED",
            }:
                raise
            skip(error.error.code.lower())
        except OSError:
            skip("unavailable")
    return {
        "items": matches,
        "truncated": bool(limits),
        "limits_reached": sorted(limits),
        "files_examined": files,
        "scan_bytes_reserved": scanned,
        "skipped": skipped,
        "mode": "literal_line",
        "column_units": "unicode_codepoints",
        "encoding": payload["encoding"],
        "consistency": "per_file_observation",
    }


def patch_batch(
    provider: "FilesystemProvider", payload: dict[str, Any], budget: "Budget"
) -> dict[str, Any]:
    prepared: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    diff_remaining = 8192
    for file in payload["files"]:
        budget.check()
        target = provider.guard.path(file["path"])
        identity = os.path.normcase(str(target))
        if identity in seen:
            raise RACPError("INVALID_ARGUMENT", "Duplicate patch target", layer="provider")
        seen.add(identity)
        if any(target == p or target.is_relative_to(p) for p in provider.protected):
            raise RACPError(
                "PATH_ACCESS_DENIED", "System patch target is protected", layer="provider"
            )
        raw, info = read_text(provider, target, 65536, budget)
        original_sha = hashlib.sha256(raw).hexdigest()
        if original_sha != file["expected_sha256"] or (
            file["expected_revision"] and file["expected_revision"] != revision(info)
        ):
            raise RACPError("PRECONDITION_FAILED", "Patch source changed", layer="provider")
        encoding = file["encoding"]
        bom = raw.startswith(codecs.BOM_UTF8) and encoding in {"utf-8", "utf-8-sig"}
        try:
            original = raw.decode("utf-8-sig" if bom or encoding == "utf-8-sig" else encoding)
            edited = original
            for edit in file["edits"]:
                budget.check()
                if edited.count(edit["old_text"]) != edit["expected_count"]:
                    raise RACPError(
                        "PRECONDITION_FAILED", "Patch occurrence count differs", layer="provider"
                    )
                edited = edited.replace(edit["old_text"], edit["new_text"], edit["expected_count"])
            encoding = "utf-8" if encoding == "utf-8-sig" else encoding
            new_raw = (codecs.BOM_UTF8 if bom else b"") + edited.encode(encoding)
        except UnicodeError:
            raise RACPError(
                "INVALID_ARGUMENT", "Patch text encoding failed", layer="provider"
            ) from None
        if len(new_raw) > 65536:
            raise RACPError("RESOURCE_EXHAUSTED", "Patched file exceeds 64 KiB", layer="provider")
        diff_lines = difflib.unified_diff(
            original.splitlines(keepends=True),
            edited.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target),
            n=3,
        )
        diff = "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in diff_lines
        ).encode("utf-8")
        preview = diff[:diff_remaining].decode("utf-8", errors="ignore")
        diff_remaining -= len(preview.encode("utf-8"))
        summary = {
            "path": str(target),
            "original_sha256": original_sha,
            "sha256": hashlib.sha256(new_raw).hexdigest(),
            "diff": preview,
            "diff_truncated": len(diff) > len(preview.encode("utf-8")),
        }
        write_payload = {
            "mode": "replace",
            "content": ("\ufeff" if bom else "") + edited,
            "encoding": encoding,
            "newline": "verbatim",
            "expected_sha256": original_sha,
            "expected_revision": revision(info),
        }
        prepared.append((target, write_payload, summary))
    if payload["dry_run"]:
        return {
            "files": [p[2] for p in prepared],
            "dry_run": True,
            "atomic_per_file": True,
            "atomic_batch": False,
            "applied": False,
        }
    completed: list[dict[str, Any]] = []
    for target, write_payload, summary in prepared:
        try:
            budget.check()
            result = provider.write(target, write_payload, budget)
            completed.append({**summary, "revision": result["revision"]})
        except (OSError, RACPError) as error:
            if not completed:
                raise
            raise RACPError(
                error.error.code if isinstance(error, RACPError) else "PATH_ACCESS_DENIED",
                "Patch batch partially applied",
                layer="provider",
                execution_state="completed",
                completed=[{"path": item["path"], "sha256": item["sha256"]} for item in completed],
                failed=str(target),
                atomic_batch=False,
                cleanup_status="partial",
            ) from error
    return {
        "files": completed,
        "dry_run": False,
        "applied": True,
        "atomic_per_file": True,
        "atomic_batch": False,
        "external_writer_atomic_cas": False,
    }
