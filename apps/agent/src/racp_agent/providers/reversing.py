import asyncio
import hashlib
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import Any

import psutil
from racp_agent.plugins.config import PluginConfig
from racp_agent.plugins.manifest import load_approved
from racp_agent.plugins.resources import BackendResource, ManagedResource
from racp_agent.plugins.supervisor import PluginSupervisor
from racp_agent.providers.filesystem import Budget
from racp_agent.providers.paths import is_link, revision
from racp_agent.providers.private import private_directory
from racp_agent.workspaces import WorkspacePaths, WorkspaceSpec
from racp_domain.models import ExecutionContext, RACPError
from racp_protocol.models import Capability, ResourceHandle, new_id
from racp_protocol.reversing import RE_MODELS
from racp_sdk.pagination import CursorCodec
from racp_sdk.security import canonical_digest


class ReversingProvider:
    def __init__(
        self,
        workspace: Path,
        root: Path,
        spool: Path,
        config: PluginConfig,
        additional: tuple[WorkspaceSpec, ...] = (),
    ) -> None:
        self.guard, self.root, self.spool = (
            WorkspacePaths(workspace, additional),
            private_directory(root),
            spool,
        )
        self.backends: dict[str, PluginSupervisor] = {}
        self.errors: list[str] = []
        self.resources: dict[str, ManagedResource] = {}
        self.changed = asyncio.Event()
        self.cursor = CursorCodec()
        self.pages: dict[int, tuple[float, str]] = {}
        self.page_sequence = 0
        self.lock = asyncio.Lock()
        self.protected: Callable[[], set[int]] = lambda: {os.getpid()}
        for installation in config.plugins:
            try:
                approved = load_approved(
                    installation.manifest, installation.sha256, frozenset(installation.permissions)
                )
                name = approved.manifest.name
                if name in self.backends:
                    raise ValueError("duplicate installed plugin name")
                if any(op.name not in RE_MODELS for op in approved.manifest.operations):
                    raise ValueError("plugin operation is absent from fixed public registry")
                self.backends[name] = PluginSupervisor(approved, partial(self.backend_state, name))
            except (OSError, ValueError, RACPError):
                self.errors.append("installed plugin validation failed")

    def backend_state(self, name: str, state: dict[str, Any]) -> None:
        if state["state"] in {"STOPPED", "STOPPING", "DEGRADED"}:
            for resource in self.resources.values():
                if resource.backend == name and resource.state == "ACTIVE":
                    resource.state = "EXPIRED"
                    resource.revision += 1
                    if resource.kind == "debugger":
                        resource.descriptor.debugger_state = "FAILED"
                    resource.stop_events.append(
                        {"state": "FAILED", "reason": "plugin_generation_ended"}
                    )
                    resource.stop_events[:] = resource.stop_events[-64:]
                    resource.event.set()
        self.changed.set()

    def capabilities(self) -> list[Capability]:
        values = []
        for namespace in ("re", "debugger"):
            operations = {namespace + ".backends"}
            for backend in self.backends.values():
                if backend.state == "READY":
                    operations.update(
                        name for name in backend.operations if name.startswith(namespace + ".")
                    )
            values.append(
                Capability(
                    name=namespace,
                    version="1.0.0",
                    operations=sorted(operations),
                    attributes={
                        "backends": [backend.status() for backend in self.backends.values()],
                        "configuration_errors": list(self.errors),
                        "resource_limit": 8,
                        "history_limit": 64,
                        "idle_ttl_seconds": 3600,
                        "plugin_crash_isolation": "subprocess",
                    },
                )
            )
        return values

    def inventory(self) -> list[ResourceHandle]:
        return [
            r.handle(self.backends[r.backend].manifest.backend_version)
            for r in self.resources.values()
        ]

    def process_events(self, allowed_locked: ManagedResource | None = None) -> None:
        for name, backend in self.backends.items():
            while not backend.events.empty():
                event = backend.events.get_nowait()
                resource = next(
                    (
                        r
                        for r in self.resources.values()
                        if r.backend == name
                        and r.instance == event.instance_id
                        and r.descriptor.resource_id == event.data.get("resource_id")
                        and r.state == "ACTIVE"
                    ),
                    None,
                )
                if resource is None:
                    continue  # Early startup/closed generation cannot select another public Handle.
                if resource.lock.locked() and resource is not allowed_locked:
                    backend.events.put_nowait(event)
                    break  # Apply stop after the command's accepted response, never overwrite it.
                try:
                    changes = {
                        k: v
                        for k, v in event.data.items()
                        if k in {"debugger_state", "stop_sequence", "stop_reason", "analysis_state"}
                    }
                    updated = BackendResource.model_validate(
                        {**resource.descriptor.model_dump(), **changes}
                    )
                    if int(updated.stop_sequence) < int(resource.descriptor.stop_sequence):
                        raise ValueError("debugger stop sequence regressed")
                    resource.descriptor = updated
                    resource.revision += 1
                    if event.kind in {"debugger.stopped", "debugger.exited"}:
                        if updated.debugger_state not in {"STOPPED", "EXITED"}:
                            raise ValueError("stop event requires STOPPED/EXITED")
                        resource.stop_events.append(
                            {
                                "sequence": updated.stop_sequence,
                                "reason": updated.stop_reason,
                                "state": updated.debugger_state,
                            }
                        )
                        resource.stop_events[:] = resource.stop_events[-64:]
                        resource.event.set()
                    self.changed.set()
                except ValueError:
                    backend.fail("PLUGIN_PROTOCOL_ERROR")

    def disk_bytes(self) -> int:
        return sum(
            path.lstat().st_size
            for path in self.root.rglob("*")
            if path.is_file() and not is_link(path.lstat())
        )

    async def probe(self) -> None:
        async def one(backend: PluginSupervisor) -> None:
            if backend.serial.locked():
                return
            try:
                await backend.health()
            except (RACPError, OSError, TimeoutError, ValueError):
                self.changed.set()

        await asyncio.gather(*(one(b) for b in self.backends.values()))

    def backend(self, name: str, operation: str) -> PluginSupervisor:
        backend = self.backends.get(name)
        if backend is None or operation not in backend.operations:
            raise RACPError(
                "OPERATION_NOT_SUPPORTED",
                "backend does not support the operation",
                layer="provider",
            )
        return backend

    def identity(
        self, id: str, context: ExecutionContext, *, closing: bool = False
    ) -> ManagedResource:
        resource = self.resources.get(id)
        if resource is None:
            raise RACPError("HANDLE_EXPIRED", "RE handle was not found", layer="provider")
        if (resource.context.principal_id, resource.context.device_id) != (
            context.principal_id,
            context.device_id,
        ):
            raise RACPError("PERMISSION_DENIED", "RE owner/Device differs", layer="provider")
        if resource.context.agent_boot_id != context.agent_boot_id:
            raise RACPError("HANDLE_EXPIRED", "RE Agent boot changed", layer="provider")
        backend = self.backends[resource.backend]
        if not closing or resource.state == "ACTIVE":
            if (
                resource.state != "ACTIVE"
                or resource.instance != backend.instance_id
                or resource.expires_monotonic <= time.monotonic()
            ):
                raise RACPError(
                    "HANDLE_EXPIRED", "RE resource expired or plugin restarted", layer="provider"
                )
        return resource

    def snapshot(
        self, path: str, directory: Path, expected: str | None, budget: Budget
    ) -> tuple[Path, str]:
        budget.check()
        source = self.guard.path(path)
        directory.mkdir(mode=0o700)
        target = directory / ("target" + source.suffix.lower())
        digest = hashlib.sha256()
        try:
            with self.guard.parent(source) as parent:
                fd = parent.open(source.name, os.O_RDONLY)
                with os.fdopen(fd, "rb") as stream, target.open("xb") as output:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size > 1024**3:
                        raise RACPError(
                            "RESOURCE_EXHAUSTED",
                            "RE target must be a regular file up to 1 GiB",
                            layer="provider",
                        )
                    size = 0
                    while chunk := stream.read(1024 * 1024):
                        budget.check()
                        size += len(chunk)
                        if size > 1024**3:
                            raise RACPError(
                                "RESOURCE_EXHAUSTED",
                                "RE target grew beyond input limit",
                                layer="provider",
                            )
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                    if revision(before) != revision(os.fstat(stream.fileno())):
                        raise RACPError(
                            "PRECONDITION_FAILED",
                            "RE source changed during snapshot",
                            layer="provider",
                        )
                    budget.check()
            sha = digest.hexdigest()
            if expected is not None and expected != sha:
                raise RACPError("PRECONDITION_FAILED", "RE target hash differs", layer="provider")
            return target, sha
        except BaseException:
            target.unlink(missing_ok=True)
            directory.rmdir()
            raise

    def safe_result(self, value: Any) -> None:
        if isinstance(value, dict):
            if set(value) & {
                "spool_path",
                "artifact_id",
                "output_id",
                "handle",
                "preview",
                "_artifact_path",
            }:
                raise RACPError(
                    "PLUGIN_PROTOCOL_ERROR",
                    "plugin cannot supply Agent file/Artifact descriptors",
                    layer="provider",
                    execution_state="unknown",
                )
            for child in value.values():
                self.safe_result(child)
        elif isinstance(value, list):
            for child in value:
                self.safe_result(child)

    async def call(
        self,
        backend: PluginSupervisor,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
        instance: str | None = None,
    ) -> dict[str, Any]:
        try:
            value = await backend.request(
                operation,
                payload,
                min(context.timeout_ms, backend.operations[operation].max_timeout_ms),
                expected_instance_id=instance,
            )
        except RACPError as exc:
            if exc.error.execution_state == "unknown" and exc.error.code.startswith("PLUGIN_"):
                raise RACPError(
                    "EXECUTION_UNKNOWN",
                    "plugin outcome uncertain; execution is not replayed",
                    layer="provider",
                    execution_state="unknown",
                    cause=exc.error.code,
                    cleanup_status=backend.last_cleanup,
                ) from exc
            raise
        except asyncio.CancelledError:
            if backend.last_cleanup != "complete":
                raise RACPError(
                    "EXECUTION_UNKNOWN",
                    "plugin cancellation cleanup unverified",
                    layer="provider",
                    execution_state="unknown",
                    cleanup_status=backend.last_cleanup,
                ) from None
            raise
        try:
            self.safe_result(value)
        except RACPError:
            backend.fail("PLUGIN_PROTOCOL_ERROR")
            await backend.finish_stop()
            raise
        return value

    def page(
        self, resource: ManagedResource, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        scope = canonical_digest(
            {
                "id": resource.id,
                "owner": resource.context.principal_id,
                "device": resource.context.device_id,
                "query": {k: v for k, v in payload.items() if k != "cursor"},
            }
        )
        token = payload.get("cursor")
        if token is None:
            return payload, scope
        index = self.cursor.decode(token, scope, str(resource.revision))
        stored = self.pages.get(index)
        if stored is None or stored[0] <= time.monotonic():
            raise RACPError("CURSOR_EXPIRED", "backend cursor cache expired", layer="provider")
        return {**payload, "cursor": stored[1]}, scope

    def next_page(self, resource: ManagedResource, value: dict[str, Any], scope: str) -> None:
        token = value.get("next_cursor")
        if token is None:
            return
        if not isinstance(token, str) or len(token) > 2048:
            raise RACPError(
                "PLUGIN_PROTOCOL_ERROR",
                "invalid backend cursor",
                layer="provider",
                execution_state="unknown",
            )
        self.pages = {k: v for k, v in self.pages.items() if v[0] > time.monotonic()}
        if len(self.pages) >= 128:
            self.pages.pop(next(iter(self.pages)))
        self.page_sequence += 1
        self.pages[self.page_sequence] = (time.monotonic() + 300, token)
        value["next_cursor"] = self.cursor.encode(self.page_sequence, scope, str(resource.revision))

    async def open(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        if (
            sum(r.state == "ACTIVE" for r in self.resources.values()) >= 8
            or len(self.resources) >= 64
        ):
            raise RACPError(
                "RESOURCE_EXHAUSTED", "RE resource/history limit reached", layer="provider"
            )
        if self.disk_bytes() >= 9 * 1024**3:
            raise RACPError(
                "RESOURCE_EXHAUSTED", "RE target/database storage admission full", layer="provider"
            )
        backend = self.backend(payload["backend"], operation)
        id = new_id("analysis" if operation == "re.open" else "debug")
        directory = self.root / id
        if operation == "debugger.attach":
            if (
                payload["agent_boot_id"] != context.agent_boot_id
                or payload["pid"] in self.protected()
            ):
                raise RACPError(
                    "PERMISSION_DENIED",
                    "protected or previous-boot attach target",
                    layer="provider",
                )
            process = psutil.Process(payload["pid"])
            if process.create_time() != payload["create_time"]:
                raise RACPError(
                    "PRECONDITION_FAILED", "attach process identity changed", layer="provider"
                )
            if os.name == "nt":
                from racp_agent.broker.identity import process_identity

                actor, peer = process_identity(os.getpid()), process_identity(process.pid)
                if (actor.sid, actor.session) != (peer.sid, peer.session):
                    raise RACPError(
                        "PERMISSION_DENIED", "attach OS identity differs", layer="provider"
                    )
            elif not self.same_uid(process):
                raise RACPError("PERMISSION_DENIED", "attach OS identity differs", layer="provider")
            path = process.exe()
        else:
            path = payload["path" if operation == "re.open" else "executable"]
        started = time.monotonic()
        budget = Budget(started + context.timeout_ms / 1000, threading.Event())
        copying = asyncio.create_task(
            asyncio.to_thread(
                self.snapshot, path, directory, payload.get("expected_sha256"), budget
            )
        )
        try:
            target, sha = await asyncio.shield(copying)
        except asyncio.CancelledError:
            budget.cancelled.set()
            while not copying.done():
                try:
                    await asyncio.shield(copying)
                except (asyncio.CancelledError, RACPError):
                    continue
            await asyncio.gather(copying, return_exceptions=True)
            if not copying.cancelled() and copying.exception() is None:
                copied, _ = copying.result()
                copied.unlink(missing_ok=True)
                directory.rmdir()
            raise
        context = replace(
            context,
            timeout_ms=max(1, context.timeout_ms - int((time.monotonic() - started) * 1000)),
        )
        arguments = dict(payload)
        if operation != "debugger.attach":
            arguments["path" if operation == "re.open" else "executable"] = str(target)
        try:
            value = await self.call(backend, operation, arguments, context)
            descriptor = BackendResource.model_validate(value)
            if descriptor.target_sha256 != sha:
                raise ValueError("backend target hash mismatch")
            if descriptor.pid is not None:
                self.check_target(backend, descriptor, operation, payload)
            if descriptor.analysis_database is not None and not await asyncio.to_thread(
                self.database_inside, descriptor.analysis_database, directory
            ):
                raise ValueError("analysis database is outside target storage")
            if any(
                r.backend == payload["backend"]
                and r.instance == backend.instance_id
                and r.descriptor.resource_id == descriptor.resource_id
                and r.state == "ACTIVE"
                for r in self.resources.values()
            ):
                raise ValueError("backend resource identity reused")
            resource = ManagedResource(
                id,
                "analysis" if operation == "re.open" else "debugger",
                payload["backend"],
                backend.instance_id,
                context,
                descriptor,
                directory,
                "borrowed" if operation == "debugger.attach" else "racp_owned",
            )
            if (
                resource.kind == "analysis"
                and descriptor.analysis_state is None
                or resource.kind == "debugger"
                and descriptor.debugger_state is None
            ):
                raise ValueError("backend resource state absent")
            self.resources[id] = resource
            if descriptor.debugger_state == "STOPPED" and int(descriptor.stop_sequence):
                resource.stop_events.append(
                    {
                        "sequence": descriptor.stop_sequence,
                        "reason": descriptor.stop_reason,
                        "state": "STOPPED",
                    }
                )
            self.changed.set()
            return {
                "analysis_id" if resource.kind == "analysis" else "debug_id": id,
                "handle": resource.handle(backend.manifest.backend_version).model_dump(),
            }
        except BaseException as exc:
            # Failure may have created an unreported backend resource. Expire the
            # entire generation instead of replaying or guessing its identity.
            backend.fail("PLUGIN_RESOURCE_OPEN_FAILED")
            await backend.finish_stop()
            if isinstance(exc, ValueError):
                raise RACPError(
                    "EXECUTION_UNKNOWN",
                    "invalid backend resource descriptor",
                    layer="provider",
                    execution_state="unknown",
                    cleanup_status=backend.last_cleanup,
                ) from exc
            raise

    @staticmethod
    def database_inside(raw: str, directory: Path) -> bool:
        path = Path(raw)
        return path.is_absolute() and path.resolve().is_relative_to(directory.resolve())

    @staticmethod
    def same_uid(process: psutil.Process) -> bool:
        uids, getuid = getattr(process, "uids", None), getattr(os, "getuid", None)
        return bool(callable(uids) and callable(getuid) and uids().real == getuid())

    @staticmethod
    def check_target(
        backend: PluginSupervisor,
        descriptor: BackendResource,
        operation: str,
        payload: dict[str, Any],
    ) -> None:
        assert descriptor.pid is not None
        if (
            descriptor.create_time is None
            or abs(psutil.Process(descriptor.pid).create_time() - descriptor.create_time) > 0.000001
        ):
            raise ValueError("backend target PID/birth differs from OS")
        if operation == "debugger.attach":
            if (descriptor.pid, descriptor.create_time) != (payload["pid"], payload["create_time"]):
                raise ValueError("attached target differs from approved identity")
            return
        if backend.owned is None:
            raise ValueError("backend has no owned containment")
        if os.name == "nt":
            import win32api
            import win32job

            handle = win32api.OpenProcess(0x101000, False, descriptor.pid)
            try:
                if abs(
                    psutil.Process(descriptor.pid).create_time() - descriptor.create_time
                ) > 0.000001 or not win32job.IsProcessInJob(handle, backend.owned.job):
                    raise ValueError("launched target is outside owned plugin Job")
            finally:
                handle.Close()
        else:
            getpgid = getattr(os, "getpgid", None)
            if getpgid is None or getpgid(descriptor.pid) != backend.owned.process.pid:
                raise ValueError("launched target is outside owned process group")

    async def execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        with self.guard.select(context.workspace_id):
            return await self.selected_execute(operation, payload, context)

    async def selected_execute(
        self, operation: str, payload: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        try:
            if operation.endswith(".backends"):
                namespace = operation.split(".")[0] + "."
                result = {
                    "backends": [
                        {
                            **b.status(),
                            "operations": sorted(
                                n for n in b.operations if n.startswith(namespace)
                            ),
                        }
                        for b in self.backends.values()
                        if any(n.startswith(namespace) for n in b.operations)
                    ],
                    "configuration_errors": list(self.errors),
                }
            elif operation in {"re.open", "debugger.launch", "debugger.attach"}:
                async with self.lock:
                    result = await self.open(operation, payload, context)
            else:
                field = "analysis_id" if operation.startswith("re.") else "debug_id"
                resource = self.identity(
                    payload[field], context, closing=operation.endswith(".close")
                )
                if (resource.kind == "analysis") != operation.startswith("re."):
                    raise RACPError(
                        "HANDLE_EXPIRED", "RE Handle type differs from operation", layer="provider"
                    )
                backend = self.backends[resource.backend]
                if operation.endswith(".keepalive"):
                    resource.renew()
                    result = {
                        "handle": resource.handle(backend.manifest.backend_version).model_dump()
                    }
                elif operation.endswith(".close") and resource.state != "ACTIVE":
                    result = {
                        "handle": resource.handle(backend.manifest.backend_version).model_dump()
                    }
                elif operation == "debugger.wait":
                    result = await self.wait(resource, int(payload["after_sequence"]), context)
                else:
                    async with resource.lock:
                        result = await self.resource_call(
                            resource, backend, operation, payload, context
                        )
            return {"state": "SUCCEEDED", "result": result, "error": None}
        except RACPError as exc:
            if exc.error.code in {"EXECUTION_UNKNOWN", "TIMEOUT", "CANCELLED"}:
                raise
            return {"state": "FAILED", "result": None, "error": asdict(exc.error)}

    async def resource_call(
        self,
        resource: ManagedResource,
        backend: PluginSupervisor,
        operation: str,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        field = "analysis_id" if resource.kind == "analysis" else "debug_id"
        if (
            operation in {"debugger.registers", "debugger.read_memory", "debugger.backtrace"}
            and resource.descriptor.debugger_state != "STOPPED"
        ):
            raise RACPError(
                "PRECONDITION_FAILED", "debugger target must be STOPPED", layer="provider"
            )
        outbound = {**payload, field: resource.descriptor.resource_id}
        if operation == "debugger.read_memory":
            return await self.memory(resource, backend, outbound, context)
        if operation == "re.query":
            outbound, scope = self.page(resource, outbound)
        result = await self.call(backend, operation, outbound, context, resource.instance)
        if operation == "re.query":
            self.next_page(resource, result, scope)
        if "debugger_state" in result:
            resource.descriptor.debugger_state = BackendResource.model_validate(
                {**resource.descriptor.model_dump(), "debugger_state": result["debugger_state"]}
            ).debugger_state
        if "analysis_state" in result:
            resource.descriptor.analysis_state = BackendResource.model_validate(
                {**resource.descriptor.model_dump(), "analysis_state": result["analysis_state"]}
            ).analysis_state
        if operation.endswith(".close"):
            resource.state = "CLOSED"
        if operation in {"re.command", "debugger.command", "re.close", "debugger.close"}:
            resource.revision += 1
        resource.renew()
        self.changed.set()
        result["handle"] = resource.handle(backend.manifest.backend_version).model_dump()
        return result

    async def wait(
        self, resource: ManagedResource, after: int, context: ExecutionContext
    ) -> dict[str, Any]:
        deadline = time.monotonic() + context.timeout_ms / 1000
        while True:
            self.process_events()
            self.identity(resource.id, context)
            if resource.stop_events:
                earliest = int(resource.stop_events[0].get("sequence", "0"))
                if after and after < earliest - 1:
                    raise RACPError(
                        "CURSOR_EXPIRED",
                        "debugger stop history gap; inspect current state",
                        layer="provider",
                    )
                event = next(
                    (e for e in resource.stop_events if int(e.get("sequence", "0")) > after), None
                )
                if event is not None:
                    return {
                        "stop_event": event,
                        "handle": resource.handle(
                            self.backends[resource.backend].manifest.backend_version
                        ).model_dump(),
                    }
            resource.event.clear()
            try:
                await asyncio.wait_for(
                    resource.event.wait(), min(0.1, max(0.001, deadline - time.monotonic()))
                )
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise RACPError(
                        "TIMEOUT",
                        "debugger stop wait timed out; target preserved",
                        layer="provider",
                    ) from None

    async def memory(
        self,
        resource: ManagedResource,
        backend: PluginSupervisor,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + context.timeout_ms / 1000
        data = bytearray()
        address, size = int(payload["address"], 16), payload["size_bytes"]
        while len(data) < size:
            self.process_events(resource)
            if resource.descriptor.debugger_state != "STOPPED":
                raise RACPError(
                    "PRECONDITION_FAILED", "target resumed during memory read", layer="provider"
                )
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise RACPError("TIMEOUT", "memory read budget elapsed", layer="provider")
            count = min(16384, size - len(data))
            response = await self.call(
                backend,
                "debugger.read_memory",
                {**payload, "address": hex(address + len(data)), "size_bytes": count},
                replace(context, timeout_ms=remaining),
                resource.instance,
            )
            try:
                chunk = bytes.fromhex(response["bytes_hex"])
                if len(chunk) != count:
                    raise ValueError("memory response length differs")
            except (ValueError, KeyError, TypeError) as exc:
                backend.fail("PLUGIN_PROTOCOL_ERROR")
                await backend.finish_stop()
                raise RACPError(
                    "EXECUTION_UNKNOWN",
                    "invalid memory result",
                    layer="provider",
                    execution_state="unknown",
                ) from exc
            data.extend(chunk)
        result: dict[str, Any] = {
            "address": hex(address),
            "size_bytes": size,
            "consistency": "stopped_target_observation",
        }
        if size <= 4096:
            result["bytes_hex"] = data.hex()
        else:
            path = self.spool / (context.operation_id + ".memory")
            with path.open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            result.update(
                spool_path=str(path),
                artifact_id=None,
                artifact_media_type="application/octet-stream",
            )
        return result

    def protected_pids(self) -> set[int]:
        values = set()
        for backend in self.backends.values():
            if backend.owned is not None:
                values.add(backend.owned.process.pid)
                try:
                    values.update(
                        p.pid
                        for p in psutil.Process(backend.owned.process.pid).children(recursive=True)
                    )
                except psutil.NoSuchProcess:
                    pass
        return values

    async def cleanup(self, *, expired_only: bool = False) -> None:
        if not expired_only:
            await asyncio.gather(*(b.finish_stop() for b in self.backends.values()))
        else:
            for resource in self.resources.values():
                if resource.state == "ACTIVE" and resource.expires_monotonic <= time.monotonic():
                    backend = self.backends[resource.backend]
                    operation = "re.close" if resource.kind == "analysis" else "debugger.close"
                    try:
                        await backend.request(
                            operation,
                            {
                                "analysis_id"
                                if resource.kind == "analysis"
                                else "debug_id": resource.descriptor.resource_id
                            },
                            5000,
                            expected_instance_id=resource.instance,
                        )
                    except (RACPError, OSError, TimeoutError):
                        await backend.finish_stop()
                    resource.state, resource.revision = "EXPIRED", resource.revision + 1
                    self.changed.set()
        for id, resource in list(self.resources.items()):
            if resource.state != "ACTIVE" and resource.expires_monotonic <= time.monotonic():
                # Keep closed analysis databases; their durable retention/backup is
                # a separate storage contract, not volatile Handle resurrection.
                if resource.kind == "debugger":
                    path = resource.directory.resolve()
                    if path.parent != self.root or is_link(resource.directory.lstat()):
                        raise PermissionError("debugger cache cleanup root differs")
                    shutil.rmtree(path)
                del self.resources[id]

    async def shutdown(self) -> None:
        await asyncio.gather(*(backend.close() for backend in self.backends.values()))
