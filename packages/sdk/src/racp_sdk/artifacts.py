import asyncio
import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from racp_domain.models import RACPError
from racp_protocol.artifacts import CHUNK_BYTES, MAX_ARTIFACT_BYTES

from racp_sdk.security import require_secure_url, tls_context


def file_digest(path: Path) -> tuple[int, str]:
    with path.open("rb") as stream:
        return os.fstat(stream.fileno()).st_size, hashlib.file_digest(stream, "sha256").hexdigest()


def checked(response: httpx.Response) -> dict[str, Any]:
    value: dict[str, Any] = response.json()
    if response.status_code >= 400:
        error = value.get("error", {})
        raise RACPError(
            error.get("code", "TRANSPORT_ERROR"),
            error.get("message", "transfer rejected"),
            layer=error.get("layer", "transport"),
            **error.get("details", {}),
        )
    return value


class ArtifactClient:
    def __init__(
        self,
        gateway: str,
        credential: str,
        device_id: str,
        *,
        operation_id: str | None = None,
        output_id: str | None = None,
        ca_file: Path | None = None,
    ) -> None:
        require_secure_url(gateway)
        self.gateway = gateway.rstrip("/")
        self.headers = {"Authorization": "Bearer " + credential}
        self.device_id, self.operation_id = device_id, operation_id
        self.output_id = output_id
        self.verify = tls_context(ca_file) or True

    async def authorize(self, http: httpx.AsyncClient, transfer_id: str) -> dict[str, Any]:
        return checked(
            await http.post(
                "/api/v1/artifact-transfers/" + transfer_id + "/authorize", headers=self.headers
            )
        )

    async def upload(
        self,
        path: Path,
        *,
        media_type: str = "application/octet-stream",
        transfer_id: str | None = None,
        transfer_created: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        progress = {"transfer_id": transfer_id} if transfer_id else {}
        try:
            return await self._upload(
                path,
                media_type=media_type,
                transfer_id=transfer_id,
                progress=progress,
                transfer_created=transfer_created,
            )
        except RACPError as exc:
            if progress:
                exc.error.details.setdefault("transfer_id", progress["transfer_id"])
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise RACPError(
                "TRANSPORT_ERROR",
                "upload interrupted; resume the same transfer",
                layer="transport",
                **progress,
            ) from exc

    async def _upload(
        self,
        path: Path,
        *,
        media_type: str,
        transfer_id: str | None,
        progress: dict[str, str],
        transfer_created: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        size, sha256 = await asyncio.to_thread(file_digest, path)
        if size > MAX_ARTIFACT_BYTES:
            raise RACPError("RESOURCE_EXHAUSTED", "file exceeds 1 GiB Artifact limit")
        async with httpx.AsyncClient(
            base_url=self.gateway, timeout=30, follow_redirects=False, verify=self.verify
        ) as http:
            if transfer_id:
                state = await self.authorize(http, transfer_id)
                if (
                    state["direction"] != "upload"
                    or state["size_bytes"] != size
                    or state["sha256"] != sha256
                    or state["device_id"] != self.device_id
                ):
                    raise RACPError("CONFLICT", "local file does not match the upload scope")
            else:
                state = checked(
                    await http.post(
                        "/api/v1/artifact-transfers",
                        headers=self.headers,
                        json={
                            "device_id": self.device_id,
                            "operation_id": self.operation_id,
                            "output_id": self.output_id,
                            "direction": "upload",
                            "size_bytes": size,
                            "sha256": sha256,
                            "media_type": media_type,
                        },
                    )
                )
                transfer_id = state["id"]
            assert transfer_id is not None
            progress["transfer_id"] = transfer_id
            if transfer_created is not None:
                transfer_created(transfer_id)
            renewed = time.monotonic()
            deadline = time.monotonic() + 600
            with path.open("rb") as source:
                offset = int(state["committed_bytes"])
                while offset < size:
                    if time.monotonic() > deadline:
                        raise RACPError(
                            "TIMEOUT", "upload deadline elapsed", transfer_id=transfer_id
                        )
                    if time.monotonic() - renewed > 480:
                        state = await self.authorize(http, transfer_id)
                        renewed = time.monotonic()
                    source.seek(offset)
                    chunk = await asyncio.to_thread(source.read, min(CHUNK_BYTES, size - offset))
                    if not chunk:
                        raise RACPError(
                            "PRECONDITION_FAILED",
                            "local source changed during upload",
                            transfer_id=transfer_id,
                        )
                    for attempt in range(3):
                        try:
                            response = await http.put(
                                "/api/v1/artifact-transfers/" + transfer_id + "/content",
                                content=chunk,
                                headers={
                                    "Authorization": "Bearer " + state["credential"],
                                    "Content-Range": (
                                        f"bytes {offset}-{offset + len(chunk) - 1}/{size}"
                                    ),
                                    "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                                },
                            )
                            if response.status_code == 410:
                                state = await self.authorize(http, transfer_id)
                                continue
                            progress = checked(response)
                            offset = int(progress["committed_bytes"])
                            break
                        except httpx.TransportError as exc:
                            # Query the same transfer to determine whether a lost ACK committed.
                            if attempt == 2:
                                raise RACPError(
                                    "TRANSPORT_ERROR",
                                    "upload interrupted; resume this transfer",
                                    layer="transport",
                                    transfer_id=transfer_id,
                                ) from exc
                            await asyncio.sleep(0.25 * (attempt + 1))
                            progress = checked(
                                await http.get(
                                    "/api/v1/artifact-transfers/" + transfer_id,
                                    headers=self.headers,
                                )
                            )
                            if int(progress["committed_bytes"]) == offset + len(chunk):
                                offset += len(chunk)
                                break
                    else:
                        raise RACPError(
                            "ARTIFACT_EXPIRED",
                            "upload authorization could not be renewed",
                            transfer_id=transfer_id,
                        )
            for attempt in range(3):
                try:
                    response = await http.post(
                        "/api/v1/artifact-transfers/" + transfer_id + "/complete",
                        headers={"Authorization": "Bearer " + state["credential"]},
                        json={"size_bytes": size, "sha256": sha256},
                    )
                    if response.status_code == 410:
                        state = await self.authorize(http, transfer_id)
                        continue
                    metadata = checked(response)
                    return {**metadata, "transfer_id": transfer_id}
                except httpx.TransportError as exc:
                    if attempt == 2:
                        raise RACPError(
                            "TRANSPORT_ERROR",
                            "completion response lost; inspect the same transfer",
                            layer="transport",
                            transfer_id=transfer_id,
                        ) from exc
                    await asyncio.sleep(0.25 * (attempt + 1))
        raise RACPError(
            "ARTIFACT_EXPIRED", "completion authorization could not be renewed", **progress
        )

    async def download(
        self, artifact_id: str, path: Path, *, resume: bool = True, transfer_id: str | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.gateway, timeout=30, follow_redirects=False, verify=self.verify
        ) as http:
            if transfer_id:
                state = await self.authorize(http, transfer_id)
                if (
                    state["direction"] != "download"
                    or state["artifact_id"] != artifact_id
                    or state["device_id"] != self.device_id
                ):
                    raise RACPError("CONFLICT", "download scope differs")
            else:
                state = checked(
                    await http.post(
                        "/api/v1/artifact-transfers",
                        headers=self.headers,
                        json={
                            "device_id": self.device_id,
                            "operation_id": self.operation_id,
                            "direction": "download",
                            "artifact_id": artifact_id,
                        },
                    )
                )
                transfer_id = state["id"]
            expected_size, sha256 = state["size_bytes"], state["sha256"]
            etag = '"' + sha256 + '"'
            deadline = time.monotonic() + 600
            if not resume and await asyncio.to_thread(path.exists):
                raise RACPError("CONFLICT", "download cache already exists")
            for attempt in range(4):
                if time.monotonic() >= deadline:
                    raise RACPError("TIMEOUT", "download deadline elapsed", transfer_id=transfer_id)
                offset = (
                    (await asyncio.to_thread(path.stat)).st_size
                    if await asyncio.to_thread(path.exists)
                    else 0
                )
                if offset > expected_size:
                    raise RACPError("CHECKSUM_MISMATCH", "local partial file exceeds Artifact size")
                if offset == expected_size and (offset or await asyncio.to_thread(path.exists)):
                    break
                try:
                    headers = {"Authorization": "Bearer " + state["credential"], "If-Range": etag}
                    if offset:
                        headers["Range"] = f"bytes={offset}-"
                    async with http.stream(
                        "GET",
                        "/api/v1/artifact-transfers/" + state["id"] + "/content",
                        headers=headers,
                    ) as response:
                        if response.status_code == 410:
                            await response.aread()
                            state = await self.authorize(http, state["id"])
                            continue
                        if response.status_code >= 400:
                            await response.aread()
                            checked(response)
                        if response.headers.get("etag") != etag or response.status_code != (
                            206 if offset else 200
                        ):
                            raise RACPError("PRECONDITION_FAILED", "download ETag/range differs")
                        if (
                            offset
                            and response.headers.get("content-range")
                            != f"bytes {offset}-{expected_size - 1}/{expected_size}"
                        ):
                            raise RACPError("PRECONDITION_FAILED", "download range was not honored")
                        with path.open("ab") as output:
                            async for chunk in response.aiter_bytes(65536):
                                offset += len(chunk)
                                if offset > expected_size:
                                    raise RACPError(
                                        "CHECKSUM_MISMATCH", "download exceeds scoped size"
                                    )
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                    break
                except httpx.TransportError as exc:
                    if attempt == 3:
                        raise RACPError(
                            "TRANSPORT_ERROR",
                            "download interrupted; partial file retained",
                            layer="transport",
                            transfer_id=transfer_id,
                        ) from exc
                    await asyncio.sleep(0.25 * (attempt + 1))
                    state = await self.authorize(http, state["id"])
            else:
                raise RACPError(
                    "ARTIFACT_EXPIRED",
                    "download authorization could not be renewed",
                    transfer_id=transfer_id,
                )
            size, actual = await asyncio.to_thread(file_digest, path)
            if size != expected_size or actual != sha256:
                raise RACPError("CHECKSUM_MISMATCH", "download hash/size differs")
            for attempt in range(3):
                try:
                    acknowledgement = await http.post(
                        "/api/v1/artifact-transfers/" + state["id"] + "/complete",
                        headers={"Authorization": "Bearer " + state["credential"]},
                        json={"size_bytes": size, "sha256": sha256},
                    )
                    if acknowledgement.status_code == 410:
                        state = await self.authorize(http, state["id"])
                        continue
                    checked(acknowledgement)
                    break
                except httpx.TransportError as exc:
                    if attempt == 2:
                        raise RACPError(
                            "TRANSPORT_ERROR",
                            "verified download acknowledgement lost; resume the same transfer",
                            layer="transport",
                            transfer_id=state["id"],
                        ) from exc
            else:
                raise RACPError(
                    "ARTIFACT_EXPIRED",
                    "download acknowledgement authorization could not be renewed",
                )
            return {
                "artifact_id": artifact_id,
                "sha256": sha256,
                "size_bytes": size,
                "transfer_id": state["id"],
            }
