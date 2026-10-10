"""Staging and apply state machine for already verified Gateway releases."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol

import httpx
from racp_domain.models import RACPError
from racp_protocol.models import Identifier, StrictModel, timestamp

from racp_gateway.update_manifest import VerifiedRelease


class StagedRelease(StrictModel):
    release_id: Identifier
    version: str
    root: Path
    package_path: Path
    manifest_sha256: str


class UpdateJob(StrictModel):
    id: Identifier
    current_version: str
    active_release_file: Path
    receipt_file: Path
    pre_update_backup_id: Identifier | None = None


class UpdateReceipt(StrictModel):
    job_id: Identifier
    release_id: Identifier
    version: str
    state: str
    phase: str
    previous_release: str | None = None
    previous_release_path: str | None = None
    error: str | None = None
    updated_at: str


class HostUpdater(Protocol):
    def stop_gateway(self) -> None: ...

    def start_gateway(self) -> None: ...

    def health(self, release: VerifiedRelease) -> bool: ...

    def restore_pre_update(self, backup_id: str) -> None: ...


Download = Callable[[str, int], bytes]


def download_verified_package(
    url: str,
    expected_size: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Download an already verified release URL without redirects or proxy inheritance."""
    if expected_size < 1:
        raise RACPError("INVALID_ARGUMENT", "update package size must be positive")
    chunks: list[bytes] = []
    received = 0
    with httpx.Client(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
        timeout=30,
    ) as client:
        with client.stream("GET", url) as response:
            if 300 <= response.status_code < 400:
                raise RACPError("INVALID_ARGUMENT", "update package redirect is not allowed")
            if response.status_code != 200:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE",
                    "update package download failed",
                    status_code=response.status_code,
                )
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) != expected_size:
                raise RACPError(
                    "CHECKSUM_MISMATCH",
                    "update package declared size differs from manifest",
                )
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > expected_size:
                    raise RACPError(
                        "RESOURCE_EXHAUSTED",
                        "update package exceeds manifest size",
                    )
                chunks.append(chunk)
    if received != expected_size:
        raise RACPError("CHECKSUM_MISMATCH", "update package size differs from manifest")
    return b"".join(chunks)


def _safe_member(info: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(info.filename.replace(chr(92), "/"))
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return False
    if ":" in path.parts[0]:
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    return not stat.S_ISLNK(mode)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Updater:
    def __init__(self, staging_root: Path, download: Download) -> None:
        self.staging_root = staging_root
        self.download = download

    def stage(self, release: VerifiedRelease) -> StagedRelease:
        root = self.staging_root / release.release_id
        if root.exists():
            metadata = root / "staged.json"
            if metadata.is_file():
                return StagedRelease.model_validate_json(metadata.read_bytes())
            raise RACPError("CONFLICT", "incomplete update staging directory already exists")
        temporary = self.staging_root / f".{release.release_id}.tmp"
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            payload = self.download(release.package_url, release.size_bytes)
            if len(payload) != release.size_bytes:
                raise RACPError("CHECKSUM_MISMATCH", "update package size differs from manifest")
            if hashlib.sha256(payload).hexdigest() != release.sha256:
                raise RACPError("CHECKSUM_MISMATCH", "update package hash differs from manifest")
            package = temporary / "package.zip"
            package.write_bytes(payload)
            extracted = temporary / "release"
            extracted.mkdir()
            total = 0
            with zipfile.ZipFile(package) as archive:
                entries = archive.infolist()
                if not entries or len(entries) > 100_000:
                    raise RACPError("INVALID_ARGUMENT", "update archive entry count is invalid")
                for info in entries:
                    if not _safe_member(info):
                        raise RACPError("INVALID_ARGUMENT", "update archive contains unsafe path")
                    total += info.file_size
                    if total > max(release.size_bytes * 20, 1024**3):
                        raise RACPError("RESOURCE_EXHAUSTED", "update archive expands beyond limit")
                    destination = (extracted / PurePosixPath(info.filename)).resolve()
                    if not destination.is_relative_to(extracted.resolve()):
                        raise RACPError("INVALID_ARGUMENT", "update archive escapes staging root")
                    if info.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)
            staged = StagedRelease(
                release_id=release.release_id,
                version=release.version,
                root=root,
                package_path=root / "package.zip",
                manifest_sha256=release.manifest_sha256,
            )
            (temporary / "staged.json").write_text(
                staged.model_dump_json(indent=2),
                encoding="utf-8",
            )
            self.staging_root.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, root)
            return staged
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def _receipt(
        self,
        job: UpdateJob,
        release: VerifiedRelease,
        *,
        state: str,
        phase: str,
        previous_release: str | None = None,
        previous_release_path: str | None = None,
        error: str | None = None,
    ) -> UpdateReceipt:
        receipt = UpdateReceipt(
            job_id=job.id,
            release_id=release.release_id,
            version=release.version,
            state=state,
            phase=phase,
            previous_release=previous_release,
            previous_release_path=previous_release_path,
            error=error,
            updated_at=timestamp(),
        )
        _write_json(job.receipt_file, receipt.model_dump(mode="json"))
        return receipt

    def apply(
        self,
        job: UpdateJob,
        release: VerifiedRelease,
        staged: StagedRelease,
        host: HostUpdater,
    ) -> UpdateReceipt:
        if staged.release_id != release.release_id or staged.version != release.version:
            raise RACPError("CONFLICT", "staged release does not match verified manifest")
        if not staged.root.is_dir():
            raise RACPError("INVALID_ARGUMENT", "staged release directory is missing")
        previous_release = None
        previous_release_path = None
        if job.receipt_file.is_file():
            recovered = UpdateReceipt.model_validate_json(job.receipt_file.read_bytes())
            if recovered.job_id != job.id or recovered.release_id != release.release_id:
                raise RACPError("CONFLICT", "update receipt belongs to another job or release")
            if recovered.state in {"SUCCEEDED", "FAILED"}:
                return recovered
            previous_release = recovered.previous_release
            previous_release_path = recovered.previous_release_path
        if job.active_release_file.is_file():
            document = json.loads(job.active_release_file.read_text(encoding="utf-8"))
            if previous_release is None:
                previous_release = str(document.get("release_id") or "") or None
                previous_release_path = str(document.get("path") or "") or None
        self._receipt(
            job,
            release,
            state="RUNNING",
            phase="staged",
            previous_release=previous_release,
            previous_release_path=previous_release_path,
        )
        try:
            host.stop_gateway()
            self._receipt(
                job,
                release,
                state="RUNNING",
                phase="stopped",
                previous_release=previous_release,
                previous_release_path=previous_release_path,
            )
            _write_json(
                job.active_release_file,
                {
                    "release_id": release.release_id,
                    "version": release.version,
                    "path": str(staged.root / "release"),
                },
            )
            self._receipt(
                job,
                release,
                state="RUNNING",
                phase="switched",
                previous_release=previous_release,
                previous_release_path=previous_release_path,
            )
            host.start_gateway()
            if not host.health(release):
                raise RuntimeError("updated Gateway failed health verification")
            return self._receipt(
                job,
                release,
                state="SUCCEEDED",
                phase="healthy",
                previous_release=previous_release,
                previous_release_path=previous_release_path,
            )
        except BaseException as exc:
            rollback_phase = "manual_recovery"
            if release.schema_rollback_compatible and previous_release is not None:
                _write_json(
                    job.active_release_file,
                    {
                        "release_id": previous_release,
                        "path": previous_release_path,
                        "rollback": True,
                    },
                )
                try:
                    host.start_gateway()
                    rollback_phase = "rollback"
                except BaseException:
                    pass
            elif not release.schema_rollback_compatible and job.pre_update_backup_id:
                try:
                    host.restore_pre_update(job.pre_update_backup_id)
                    if previous_release is not None:
                        _write_json(
                            job.active_release_file,
                            {
                                "release_id": previous_release,
                                "path": previous_release_path,
                                "rollback": True,
                            },
                        )
                    host.start_gateway()
                    rollback_phase = "rollback"
                except BaseException:
                    pass
            return self._receipt(
                job,
                release,
                state="FAILED",
                phase=rollback_phase,
                previous_release=previous_release,
                previous_release_path=previous_release_path,
                error=str(exc)[:2048],
            )
