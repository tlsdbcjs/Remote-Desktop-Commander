import asyncio
import json
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import MAX_MESSAGE_BYTES, new_id
from racp_protocol.plugins import PluginCancel, PluginEvent, PluginRequest, PluginResult

from racp_agent.plugins.manifest import (
    ApprovedPlugin,
    decode_frame,
    encode_frame,
    validate_payload,
)
from racp_agent.plugins.process import OwnedPluginProcess


class PluginSupervisor:
    """One local approved plugin, bounded stdio, no replay after uncertain execution."""

    def __init__(
        self,
        approved: ApprovedPlugin,
        on_state: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.approved, self.manifest = approved, approved.manifest
        self.operations = {operation.name: operation for operation in self.manifest.operations}
        self.on_state = on_state
        self.state, self.instance_id = "STOPPED", new_id("provider")
        self.revision = 1
        self.failure: str | None = None
        self.closed, self.started_once = False, False
        self.restarts: deque[float] = deque()
        self.next_start, self.backoff, self.ready_since = 0.0, 0.5, 0.0
        self.failed_instance: str | None = None
        self.lifecycle, self.serial = asyncio.Lock(), asyncio.Lock()
        self.admitted = 0
        self.owned: OwnedPluginProcess | None = None
        self.tasks: list[asyncio.Task[None]] = []
        self.pending: dict[str, asyncio.Future[PluginResult]] = {}
        self.events: asyncio.Queue[PluginEvent] = asyncio.Queue(64)
        self.event_sequence = 0
        self.stderr_tail = bytearray()
        self.stderr_bytes = 0
        self.last_cleanup = "complete"
        self.notification_failed = False

    def status(self) -> dict[str, Any]:
        return {
            "name": self.manifest.name,
            "backend": self.manifest.backend_name,
            "backend_version": self.manifest.backend_version,
            "state": self.state,
            "instance_id": self.instance_id,
            "revision": str(self.revision),
            "failure": self.failure,
            "cleanup_status": self.last_cleanup,
            "admitted": self.admitted,
            "queued_events": self.events.qsize(),
            "stderr_bytes": self.stderr_bytes,
            "notification_failed": self.notification_failed,
            "retry_after_ms": max(0, int((self.next_start - time.monotonic()) * 1000)),
        }

    def notify(self) -> None:
        self.revision += 1
        if self.on_state is not None:
            try:
                self.on_state(self.status())
            except Exception:
                self.notification_failed = True

    def fail(self, code: str) -> None:
        if self.state in {"STOPPING", "STOPPED"}:
            return
        self.failure, self.state = code, "DEGRADED"
        self.notify()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    RACPError(
                        code,
                        "plugin failed; execution is not replayed",
                        layer="plugin",
                        execution_state="unknown",
                    )
                )
        if self.owned is not None:
            try:
                self.owned.terminate()
            except Exception:
                self.last_cleanup = "unverified"

    async def read_stdout(self, owned: OwnedPluginProcess, instance: str) -> None:
        assert owned.process.stdout is not None
        try:
            while raw := await owned.process.stdout.readline():
                message = decode_frame(raw)
                if message.instance_id != instance:
                    raise ValueError("plugin generation differs")
                if isinstance(message, PluginResult):
                    future = self.pending.get(message.request_id)
                    if future is None or future.done():
                        raise ValueError("unsolicited or duplicate plugin result")
                    future.set_result(message)
                elif isinstance(message, PluginEvent):
                    if len(raw) > 16384 or int(message.sequence) != self.event_sequence + 1:
                        raise ValueError("plugin event exceeds budget or sequence gap")
                    self.event_sequence = int(message.sequence)
                    self.events.put_nowait(message)
                else:
                    raise ValueError("plugin stdout is result/event only")
            self.fail("PLUGIN_EXITED")
        except asyncio.CancelledError:
            raise
        except asyncio.QueueFull:
            self.fail("RESOURCE_EXHAUSTED")
        except Exception:
            self.fail("PLUGIN_PROTOCOL_ERROR")

    async def read_stderr(self, owned: OwnedPluginProcess) -> None:
        assert owned.process.stderr is not None
        try:
            while chunk := await owned.process.stderr.read(8192):
                self.stderr_bytes += len(chunk)
                self.stderr_tail.extend(chunk)
                del self.stderr_tail[:-65536]
                if self.stderr_bytes > 8 * 1024**2:
                    self.fail("RESOURCE_EXHAUSTED")
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fail("PLUGIN_IO_ERROR")

    async def monitor(self, owned: OwnedPluginProcess) -> None:
        while owned is self.owned:
            if self.failure or not owned.running():
                if not self.failure:
                    self.fail("PLUGIN_EXITED")
                await self.stop()
                return
            await asyncio.sleep(0.1)

    async def start(self) -> None:
        async with self.lifecycle:
            if self.closed:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE", "plugin supervisor closed", layer="plugin"
                )
            if self.state == "READY" and self.owned is not None and self.owned.running():
                return
            if self.owned is not None:
                await self.stop_locked()
            now = time.monotonic()
            while self.restarts and self.restarts[0] <= now - 60:
                self.restarts.popleft()
            if self.started_once:
                if len(self.restarts) >= 3:
                    self.state, self.failure = "DEGRADED", "PLUGIN_RESTART_LIMIT"
                    self.notify()
                    raise RACPError(
                        "CAPABILITY_UNAVAILABLE",
                        "plugin restart limit reached",
                        layer="plugin",
                        retry_after_ms=max(1, int((self.restarts[0] + 60 - now) * 1000)),
                    )
            if self.next_start > now:
                await asyncio.sleep(self.next_start - now)
            if self.started_once:
                self.restarts.append(time.monotonic())
            self.started_once = True
            self.instance_id = new_id("provider")
            self.failure, self.state = None, "STARTING"
            self.ready_since = 0.0
            self.stderr_tail.clear()
            self.stderr_bytes, self.event_sequence = 0, 0
            while not self.events.empty():
                self.events.get_nowait()
            self.notify()
            try:
                self.owned = await OwnedPluginProcess.start(self.manifest)
                owned = self.owned
                self.tasks = [
                    asyncio.create_task(self.read_stdout(owned, self.instance_id)),
                    asyncio.create_task(self.read_stderr(owned)),
                    asyncio.create_task(self.monitor(owned)),
                ]
                result = await self.rpc(
                    "plugin.health", {}, time.monotonic() + self.manifest.health_timeout_ms / 1000
                )
                if result != {
                    "manifest_sha256": self.approved.sha256,
                    "backend_version": self.manifest.backend_version,
                }:
                    raise RACPError(
                        "PLUGIN_SCHEMA_MISMATCH",
                        "plugin health differs from approved manifest",
                        layer="plugin",
                    )
                if self.failure or not owned.running():
                    raise RACPError(
                        "PLUGIN_EXITED", "plugin exited during readiness", layer="plugin"
                    )
                self.state = "READY"
                self.ready_since = time.monotonic()
                self.notify()
            except BaseException as exc:
                if (
                    isinstance(exc, RACPError)
                    and exc.error.details.get("cleanup_status") == "unverified"
                ):
                    self.last_cleanup = "unverified"
                self.failure = self.failure or (
                    exc.error.code if isinstance(exc, RACPError) else "PLUGIN_START_FAILED"
                )
                await self.finish_stop(locked=True)
                raise

    async def rpc(self, operation: str, payload: dict[str, Any], deadline: float) -> dict[str, Any]:
        assert self.owned is not None and self.owned.process.stdin is not None
        writer = self.owned.process.stdin
        request = PluginRequest(
            instance_id=self.instance_id,
            request_id=new_id("req"),
            operation=operation,
            payload=payload,
            deadline_unix_ms=int((time.time() + max(0, deadline - time.monotonic())) * 1000),
        )
        frame = encode_frame(request)
        future: asyncio.Future[PluginResult] = asyncio.get_running_loop().create_future()
        self.pending[request.request_id] = future
        sent = False
        try:
            async with asyncio.timeout_at(deadline):
                writer.write(frame)
                sent = True
                await writer.drain()
                message = await asyncio.shield(future)
            if message.error is not None:
                raise RACPError(
                    message.error.code,
                    message.error.message,
                    layer="plugin",
                    execution_state=message.error.execution_state,
                )
            assert message.result is not None
            return message.result
        except (TimeoutError, asyncio.CancelledError):
            if sent:
                try:
                    async with asyncio.timeout(0.25):
                        writer.write(
                            encode_frame(
                                PluginCancel(
                                    instance_id=self.instance_id, request_id=request.request_id
                                )
                            )
                        )
                        await writer.drain()
                except (TimeoutError, OSError):
                    pass
            raise
        finally:
            self.pending.pop(request.request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Consume any simultaneous EOF/protocol failure.

    async def request(
        self,
        operation: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        expected_instance_id: str | None = None,
    ) -> dict[str, Any]:
        specification = self.operations.get(operation)
        if specification is None:
            raise RACPError(
                "OPERATION_NOT_SUPPORTED", "operation absent from approved plugin", layer="plugin"
            )
        if not 1 <= timeout_ms <= specification.max_timeout_ms:
            raise RACPError(
                "INVALID_ARGUMENT", "plugin timeout exceeds operation budget", layer="plugin"
            )
        validate_payload(specification.input_schema, payload)
        # Leave room for the common request envelope before any process is started.
        if (
            len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode())
            > MAX_MESSAGE_BYTES - 8192
        ):
            raise RACPError("INVALID_ARGUMENT", "plugin input exceeds frame budget", layer="plugin")
        if self.admitted >= 64:
            raise RACPError("RESOURCE_EXHAUSTED", "plugin request queue full", layer="plugin")
        self.admitted += 1
        dispatched = False
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            async with asyncio.timeout_at(deadline):
                async with self.serial:
                    if expected_instance_id is not None and (
                        expected_instance_id != self.instance_id or self.state != "READY"
                    ):
                        raise RACPError(
                            "HANDLE_EXPIRED", "plugin generation changed", layer="plugin"
                        )
                    await self.start()
                    dispatched = True
                    try:
                        result = await self.rpc(operation, payload, deadline)
                    except RACPError:
                        if self.failure:
                            await self.finish_stop()
                        raise
                    except (TimeoutError, asyncio.CancelledError, OSError):
                        self.failure = "PLUGIN_CANCELLED"
                        await self.finish_stop()
                        raise
                    try:
                        validate_payload(specification.output_schema, result)
                    except RACPError as exc:
                        self.fail("PLUGIN_PROTOCOL_ERROR")
                        await self.finish_stop()
                        raise RACPError(
                            "PLUGIN_PROTOCOL_ERROR",
                            "plugin result violates approved schema",
                            layer="plugin",
                            execution_state="unknown",
                        ) from exc
                    return result
        except TimeoutError as exc:
            raise RACPError(
                "TIMEOUT",
                "plugin deadline expired",
                layer="plugin",
                execution_state="unknown" if dispatched else "not_started",
                cleanup_status=self.last_cleanup if dispatched else "not_needed",
            ) from exc
        finally:
            self.admitted -= 1

    async def stop_locked(self) -> None:
        self.state = "STOPPING"
        self.notify()
        current = asyncio.current_task()
        tasks, self.tasks = self.tasks, []
        for task in tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not current), return_exceptions=True
        )
        if self.owned is not None:
            owned, self.owned = self.owned, None
            try:
                await owned.stop()
                self.last_cleanup = owned.cleanup_status
            except Exception:
                self.last_cleanup = "unverified"
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    RACPError(
                        "PLUGIN_EXITED", "plugin stopped", layer="plugin", execution_state="unknown"
                    )
                )
        if self.failure and self.failed_instance != self.instance_id:
            if self.ready_since and time.monotonic() - self.ready_since >= 30:
                self.backoff = 0.5
            self.next_start = time.monotonic() + self.backoff
            self.backoff = min(30, self.backoff * 2)
            self.failed_instance = self.instance_id
        self.state = "DEGRADED" if self.failure else "STOPPED"
        self.notify()

    async def stop(self) -> None:
        async with self.lifecycle:
            await self.stop_locked()

    async def finish_stop(self, *, locked: bool = False) -> None:
        cleanup = asyncio.create_task(self.stop_locked() if locked else self.stop())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        await cleanup

    async def close(self) -> None:
        self.closed = True
        await self.finish_stop()

    async def health(self) -> None:
        async with self.serial:
            await self.start()
            try:
                value = await self.rpc(
                    "plugin.health", {}, time.monotonic() + self.manifest.health_timeout_ms / 1000
                )
                if value != {
                    "manifest_sha256": self.approved.sha256,
                    "backend_version": self.manifest.backend_version,
                }:
                    raise RACPError(
                        "PLUGIN_SCHEMA_MISMATCH", "plugin health changed", layer="plugin"
                    )
            except (RACPError, OSError, TimeoutError, asyncio.CancelledError):
                self.fail("PLUGIN_HEALTH_FAILED")
                await self.finish_stop()
                raise
