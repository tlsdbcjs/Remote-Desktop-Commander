"""Lifecycle for fixed contained workers producing one private Artifact."""

import asyncio
import json
import os
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from racp_agent.plugins.process import ContainedCommand, OwnedPluginProcess
from racp_agent.providers.paths import PathGuard
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import new_id


class OwnedArtifactRecipe:
    suffix: str
    media_type: str

    def __init__(self, spool: Path) -> None:
        self.spool = spool
        spool.mkdir(parents=True, exist_ok=True)
        self.guard = PathGuard(spool)
        self.spool = self.guard.root
        self.active: set[OwnedPluginProcess] = set()
        self.pending: dict[str, Path] = {}

    def validate(self, payload: dict[str, Any], context: ExecutionContext) -> Any:
        raise NotImplementedError

    def command(self, data: Any, path: Path) -> ContainedCommand:
        raise NotImplementedError

    def verify(self, path: Path, receipt: dict[str, Any], data: Any) -> None:
        raise NotImplementedError

    def protected_pids(self) -> set[int]:
        pids = {owned.process.pid for owned in self.active}
        if os.name == "nt":
            import win32job

            for owned in self.active:
                if owned.job is not None:
                    pids.update(
                        win32job.QueryInformationJobObject(
                            owned.job, win32job.JobObjectBasicProcessIdList
                        )
                    )
        return pids

    def remove(self, path: Path) -> None:
        with self.guard.parent(path) as parent:
            try:
                parent.unlink(path.name)
            except FileNotFoundError:
                pass

    def release(self, operation_id: str, *, preserve: bool) -> None:
        path = self.pending.pop(operation_id, None)
        if path is not None and not preserve:
            self.remove(path)

    async def execute(
        self, payload: dict[str, Any], context: ExecutionContext, gate: Callable[[], None]
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "Windows capture backend required", layer="provider"
            )
        data = self.validate(payload, context)
        path = self.spool / (context.operation_id + "." + new_id("collection") + self.suffix)
        owned = None
        readers: list[asyncio.Task[bytes]] = []
        waited = None
        complete = False
        pins = ExitStack()

        async def verify_output(receipt: dict[str, Any]) -> None:
            verification = asyncio.create_task(asyncio.to_thread(self.verify, path, receipt, data))
            interrupted = False
            while not verification.done():
                try:
                    await asyncio.shield(verification)
                except asyncio.CancelledError:
                    interrupted = True
            if interrupted:
                # Consume any failure, but preserve the caller/deadline cancellation.
                verification.exception()
                raise asyncio.CancelledError
            verification.result()

        async def read(stream: asyncio.StreamReader | None) -> bytes:
            assert stream is not None
            output = bytearray()
            while chunk := await stream.read(1024):
                output.extend(chunk)
                if len(output) > 8192:
                    raise RACPError(
                        "RESOURCE_EXHAUSTED", "Collection receipt exceeded bound", layer="provider"
                    )
            return bytes(output)

        try:
            gate()
            pins.enter_context(self.guard.directory(self.spool))
            # Pin the private spool ancestors while the worker creates its exclusive output.
            with self.guard.directory(self.spool):
                try:
                    async with asyncio.timeout(context.timeout_ms / 1000):
                        owned = await OwnedPluginProcess.start(self.command(data, path))
                        self.active.add(owned)
                        readers = [
                            asyncio.create_task(read(s))
                            for s in (owned.process.stdout, owned.process.stderr)
                        ]
                        waited = asyncio.create_task(owned.process.wait())
                        while not waited.done():
                            gate()
                            for reader in readers:
                                if reader.done() and reader.exception():
                                    reader.result()
                            await asyncio.wait([waited], timeout=0.05)
                        stdout, _ = await asyncio.gather(*readers)
                        gate()
                        try:
                            receipt = json.loads(stdout)
                        except (ValueError, UnicodeError):
                            raise RACPError(
                                "CAPABILITY_UNAVAILABLE",
                                "Collection worker returned no receipt",
                                layer="provider",
                            ) from None
                        if (
                            not isinstance(receipt, dict)
                            or receipt.get("status") != "SUCCEEDED"
                            or waited.result()
                        ):
                            code = (
                                receipt["code"]
                                if isinstance(receipt, dict)
                                and receipt.get("code")
                                in {
                                    "PERMISSION_DENIED",
                                    "PRECONDITION_FAILED",
                                    "RESOURCE_EXHAUSTED",
                                    "PROCESS_NOT_FOUND",
                                    "CAPABILITY_UNAVAILABLE",
                                }
                                else "PERMISSION_DENIED"
                                if isinstance(receipt, dict)
                                and receipt.get("winerror") in {5, 10013}
                                else "CAPABILITY_UNAVAILABLE"
                            )
                            reason = receipt.get("reason") if isinstance(receipt, dict) else None
                            raise RACPError(
                                code,
                                "Collection backend did not produce usable packets",
                                layer="provider",
                                reason=reason,
                            )
                        await verify_output(receipt)
                        gate()
                except TimeoutError:
                    raise RACPError(
                        "TIMEOUT", "Collection exceeded request budget", layer="provider"
                    ) from None
            complete = True
        finally:

            async def clean() -> None:
                for task in [*readers, *([waited] if waited is not None else [])]:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    *readers, *([waited] if waited is not None else []), return_exceptions=True
                )
                if owned is not None:
                    await owned.stop()
                    self.active.discard(owned)
                    if owned.cleanup_status != "complete":
                        self.remove(path)
                        raise RACPError(
                            "EXECUTION_UNKNOWN",
                            "Collection cleanup unverified",
                            layer="provider",
                            cleanup_status="unverified",
                        )

            cleanup = asyncio.create_task(clean())
            cancelled = False
            try:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                        complete = False
                await cleanup
                if not complete:
                    self.remove(path)
            finally:
                pins.close()
            if cancelled:
                raise asyncio.CancelledError
        self.pending[context.operation_id] = path
        return {
            "state": "SUCCEEDED",
            "error": None,
            "result": {
                **receipt,
                "device_id": context.device_id,
                "agent_boot_id": context.agent_boot_id,
                "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "spool_path": str(path),
                "artifact_media_type": self.media_type,
                "cleanup_status": "complete",
                "artifact_id": None,
            },
        }
