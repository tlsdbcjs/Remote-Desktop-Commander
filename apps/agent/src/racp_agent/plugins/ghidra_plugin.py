"""Fixed local Ghidra adapter. Only RACP frames are written to stdout."""

import argparse
import asyncio
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import new_id
from racp_protocol.plugins import (
    PluginCancel,
    PluginError,
    PluginManifest,
    PluginRequest,
    PluginResult,
)
from racp_protocol.registry import validate_payload

from racp_agent.plugins.ghidra_driver import GhidraDriver
from racp_agent.plugins.ghidra_runtime import GhidraRuntime, file_hash
from racp_agent.plugins.manifest import decode_frame, encode_frame


class GhidraPlugin:
    def __init__(self, manifest: Path, catalog: Path, approved_sha256: str) -> None:
        self.manifest = manifest
        self.runtime = GhidraRuntime(catalog, approved_sha256)
        specification = PluginManifest.model_validate_json(manifest.read_bytes())
        self.driver = GhidraDriver(
            self.runtime.catalog.installation, self.runtime.catalog.java, specification
        )
        self.instance: str | None = None
        self.java_checked = False
        self.sessions: dict[str, Path] = {}

    async def health(self) -> dict[str, Any]:
        if os.name != "nt":
            raise RACPError(
                "CAPABILITY_UNAVAILABLE",
                "Ghidra subprocess containment currently requires Windows Job Objects",
                layer="plugin",
            )
        await asyncio.to_thread(self.runtime.verify)
        if not self.java_checked:
            code = await self.driver.process(
                [str(self.runtime.catalog.java), "-version"],
                Path(self.driver.manifest.working_directory),
            )
            match = re.search(rb'version "(\d+)', self.driver.log)
            if code or match is None or int(match[1]) < 21:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE", "Ghidra requires JDK 21 or later", layer="plugin"
                )
            self.java_checked = True
        return {
            "manifest_sha256": hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            "backend_version": self.runtime.catalog.version,
        }

    async def execute(self, request: PluginRequest) -> dict[str, Any]:
        if self.instance is None:
            self.instance = request.instance_id
        elif self.instance != request.instance_id:
            raise RACPError("HANDLE_EXPIRED", "Ghidra generation changed", layer="plugin")
        if request.operation == "plugin.health":
            return await self.health()
        payload = validate_payload(request.operation, request.payload)
        seconds = max(0.001, request.deadline_unix_ms / 1000 - time.time())
        if request.operation == "re.open":
            if len(self.sessions) >= 8:
                raise RACPError(
                    "RESOURCE_EXHAUSTED", "Ghidra session limit reached", layer="plugin"
                )
            await asyncio.to_thread(self.runtime.verify, force=True)
            target = Path(payload["path"])
            digest = await asyncio.to_thread(file_hash, target)
            info = await self.driver.call(target, {"action": "info"}, seconds, opening=True)
            resource = new_id("ghidra")
            self.sessions[resource] = target
            return {
                "resource_id": resource,
                "target_sha256": digest,
                "architecture": info["architecture"],
                "image_base": info["image_base"],
                "analysis_state": "READY",
                "analysis_database": str(target.parent / "database/analysis.gpr"),
            }
        found = self.sessions.get(payload["analysis_id"])
        if found is None:
            raise RACPError("HANDLE_EXPIRED", "Ghidra analysis resource absent", layer="plugin")
        target = found
        if request.operation == "re.close":
            del self.sessions[payload["analysis_id"]]
            return {"closed": True, "analysis_state": "CLOSED"}
        if request.operation not in {"re.query", "re.command"}:
            raise RACPError(
                "OPERATION_NOT_SUPPORTED", "Ghidra operation unsupported", layer="plugin"
            )
        await asyncio.to_thread(self.runtime.verify, force=True)
        return await self.driver.call(target, payload, seconds)

    async def run(self) -> None:
        while raw := await asyncio.to_thread(sys.stdin.buffer.readline, 1024 * 1024 + 1):
            request = decode_frame(raw)
            if isinstance(request, PluginCancel):
                continue  # Parent-owned Job/group is the active-call cancellation boundary.
            if not isinstance(request, PluginRequest):
                raise ValueError("Ghidra stdin is request/cancel only")
            try:
                async with asyncio.timeout(
                    max(0.001, request.deadline_unix_ms / 1000 - time.time())
                ):
                    result = await self.execute(request)
                reply = PluginResult(
                    instance_id=request.instance_id,
                    request_id=request.request_id,
                    state="SUCCEEDED",
                    result=result,
                )
            except RACPError as exception:
                reply = PluginResult(
                    instance_id=request.instance_id,
                    request_id=request.request_id,
                    state="FAILED",
                    error=PluginError.model_validate(
                        {
                            "code": exception.error.code,
                            "message": exception.error.message,
                            "execution_state": exception.error.execution_state,
                        }
                    ),
                )
            sys.stdout.buffer.write(encode_frame(reply))
            sys.stdout.buffer.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local allowlisted RACP Ghidra plugin")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-catalog", type=Path, required=True)
    parser.add_argument("--runtime-sha256", required=True)
    args = parser.parse_args()
    if not args.manifest.is_absolute() or not args.runtime_catalog.is_absolute():
        parser.error("manifest and runtime catalog must be absolute local paths")
    if not re.fullmatch(r"[a-f0-9]{64}", args.runtime_sha256):
        parser.error("runtime catalog hash must be SHA-256")
    asyncio.run(GhidraPlugin(args.manifest, args.runtime_catalog, args.runtime_sha256).run())


if __name__ == "__main__":
    main()
