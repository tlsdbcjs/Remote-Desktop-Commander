"""Broker input ownership and observation fences, independent of transport and Win32."""

import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from pydantic import Field, ValidationError
from racp_domain.models import RACPError
from racp_protocol.desktop import DESKTOP_MODELS, DESKTOP_READS
from racp_protocol.models import Identifier, StrictModel, new_id, timestamp

from racp_agent.broker.blobs import CaptureBlobs


class BrokerContext(StrictModel):
    owner_id: Identifier
    device_id: Identifier
    operation_id: Identifier
    timeout_ms: int = Field(default=3000, ge=1, le=30000)


class BrokerRequest(StrictModel):
    operation: str
    payload: dict[str, Any]
    context: BrokerContext


class DesktopBackend(Protocol):
    def status(self) -> dict[str, Any]: ...
    def layout(self) -> dict[str, Any]: ...
    def foreground(self) -> str | None: ...
    def input_tick(self) -> int: ...
    def foreground_tick(self) -> int: ...
    def acquire_lease(self) -> None: ...
    def release_lease(self) -> None: ...
    def windows(self, limit: int) -> list[dict[str, Any]]: ...
    def validate_window(self, window: dict[str, Any]) -> bool: ...
    def release_inputs(self) -> None: ...
    def action(self, operation: str, payload: dict[str, Any], guard: Any) -> dict[str, Any]: ...
    def capture(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def inspect(self, payload: dict[str, Any], observation: str) -> dict[str, Any]: ...
    def clear_observations(self) -> None: ...
    def attach_guard(self, raw: dict[str, Any]) -> dict[str, Any]: ...
    def guard_info(self) -> dict[str, Any]: ...


@dataclass
class Lease:
    id: str
    owner: str
    device: str
    expires: float
    foreground: str | None
    input_tick: int
    foreground_tick: int


@dataclass
class Observation:
    owner: str
    device: str
    expires: float
    layout: str
    windows: dict[str, dict[str, Any]]


class BrokerCore:
    def __init__(self, session_id: int, backend: DesktopBackend) -> None:
        self.session_id, self.backend = session_id, backend
        self.lease: Lease | None = None
        self.observations: dict[str, Observation] = {}
        self.captures = CaptureBlobs()

    def available(self) -> None:
        status = self.backend.status()
        if not status["available"]:
            self.abort()
            raise RACPError(status["error_code"], status["reason"], layer="broker")

    def abort(self) -> None:
        try:
            self.backend.release_inputs()
        finally:
            try:
                self.backend.release_lease()
            finally:
                self.lease = None
                self.observations.clear()
                self.backend.clear_observations()

    def tick(self) -> None:
        self.captures.collect()
        self.observations = {
            key: value
            for key, value in self.observations.items()
            if value.expires > time.monotonic()
        }
        if self.lease is not None:
            try:
                self.check_lease()
            except RACPError:
                pass

    def check_lease(self) -> Lease:
        lease = self.lease
        if lease is None or lease.expires <= time.monotonic():
            self.abort()
            raise RACPError("LEASE_EXPIRED", "desktop input lease expired", layer="broker")
        self.available()
        if (
            self.backend.input_tick() != lease.input_tick
            or self.backend.foreground() != lease.foreground
            or self.backend.foreground_tick() != lease.foreground_tick
        ):
            self.abort()
            raise RACPError("STALE_OBSERVATION", "foreground or user input changed", layer="broker")
        return lease

    def scoped_lease(self, payload: dict[str, Any], context: BrokerContext) -> Lease:
        lease = self.lease
        if (
            lease is None
            or lease.id != payload["lease_id"]
            or lease.owner != context.owner_id
            or lease.device != context.device_id
        ):
            raise RACPError("PERMISSION_DENIED", "desktop lease scope mismatch", layer="broker")
        return self.check_lease()

    def observe(self, context: BrokerContext, layout: dict[str, Any]) -> dict[str, Any]:
        self.tick()
        if len(self.observations) >= 32:
            raise RACPError("RESOURCE_EXHAUSTED", "desktop observation limit", layer="broker")
        windows = self.backend.windows(128)
        identifier = new_id("observation")
        self.observations[identifier] = Observation(
            context.owner_id,
            context.device_id,
            time.monotonic() + 5,
            layout["layout_revision"],
            {w["window_id"]: w for w in windows},
        )
        return {"observation_id": identifier, "captured_at": timestamp(), "ttl_ms": 5000, **layout}

    def guard(
        self, payload: dict[str, Any], context: BrokerContext, *, activate: bool = False
    ) -> Lease:
        lease = self.scoped_lease(payload, context)
        observation = self.observations.get(payload["observation_id"])
        if (
            observation is None
            or observation.expires <= time.monotonic()
            or observation.owner != context.owner_id
            or observation.device != context.device_id
            or observation.layout != payload["layout_revision"]
            or observation.layout != self.backend.layout()["layout_revision"]
        ):
            self.abort()
            raise RACPError(
                "STALE_OBSERVATION", "desktop observation changed or expired", layer="broker"
            )
        window = observation.windows.get(payload["expected_window_id"])
        try:
            valid = window is not None and self.backend.validate_window(window)
        except RACPError:
            self.abort()
            raise
        if window is None or not valid:
            self.abort()
            raise RACPError(
                "STALE_OBSERVATION", "observed window identity or bounds changed", layer="broker"
            )
        if not activate and window["window_id"] != lease.foreground:
            self.abort()
            raise RACPError(
                "STALE_OBSERVATION", "intended window is not foreground", layer="broker"
            )
        return lease

    def execute(
        self, operation: str, payload: dict[str, Any], context: BrokerContext
    ) -> dict[str, Any]:
        if operation == "broker.status" and not payload:
            return self.backend.status()
        if operation == "broker.guard_info" and not payload:
            return self.backend.guard_info()
        if operation == "broker.guard_attach":
            if self.lease is not None:
                raise RACPError(
                    "RESOURCE_BUSY", "input lease active during guardian attach", layer="broker"
                )
            return self.backend.attach_guard(payload)
        if operation == "broker.abort" and not payload:
            self.abort()
            self.captures.blobs.clear()
            return {"released": True}
        if operation in {"broker.capture_read", "broker.capture_release"}:
            args = (payload, context.owner_id, context.device_id, context.operation_id)
            if operation.endswith("read"):
                return self.captures.read(*args)
            self.captures.release(*args)
            return {"released": True}
        if operation not in DESKTOP_MODELS or operation == "desktop.sessions":
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "operation not allowed in Broker", layer="broker"
            )
        payload = DESKTOP_MODELS[operation].model_validate(payload).model_dump()
        if payload["session_id"] != self.session_id:
            raise RACPError("PERMISSION_DENIED", "Broker session mismatch", layer="broker")
        self.available()
        if operation in DESKTOP_READS or operation == "desktop.lease_acquire":
            self.tick()
        if operation == "desktop.lease_acquire":
            if self.lease is not None:
                raise RACPError(
                    "RESOURCE_BUSY", "session already has an input lease", layer="broker"
                )
            self.backend.acquire_lease()
            try:
                self.lease = Lease(
                    new_id("lease"),
                    context.owner_id,
                    context.device_id,
                    time.monotonic() + payload["ttl_ms"] / 1000,
                    self.backend.foreground(),
                    self.backend.input_tick(),
                    self.backend.foreground_tick(),
                )
            except BaseException:
                self.abort()
                raise
            return {
                "lease_id": self.lease.id,
                "ttl_ms": payload["ttl_ms"],
                "session_id": self.session_id,
            }
        if operation in {"desktop.lease_renew", "desktop.lease_release"}:
            lease = self.scoped_lease(payload, context)
            if operation.endswith("release"):
                self.abort()
                return {"released": True}
            lease.expires = time.monotonic() + payload["ttl_ms"] / 1000
            return {"lease_id": lease.id, "ttl_ms": payload["ttl_ms"]}
        if operation in {
            "desktop.monitors",
            "desktop.windows",
            "desktop.foreground",
            "desktop.screenshot",
            "desktop.inspect",
        }:
            layout = self.backend.layout()
            metadata = self.observe(context, layout)
            if operation == "desktop.windows":
                windows = list(self.observations[metadata["observation_id"]].windows.values())
                return {
                    **metadata,
                    "windows": windows[: payload["limit"]],
                    "truncated": len(windows) > payload["limit"],
                }
            if operation == "desktop.foreground":
                return {**metadata, "window_id": self.backend.foreground()}
            if operation == "desktop.inspect":
                window = self.observations[metadata["observation_id"]].windows.get(
                    payload["window_id"]
                )
                if window is None or not self.backend.validate_window(window):
                    raise RACPError(
                        "STALE_OBSERVATION", "inspection window unavailable", layer="broker"
                    )
                result = self.backend.inspect(payload, metadata["observation_id"])
                if self.backend.layout()["layout_revision"] != metadata[
                    "layout_revision"
                ] or not self.backend.validate_window(window):
                    self.abort()
                    raise RACPError(
                        "STALE_OBSERVATION", "window changed during inspection", layer="broker"
                    )
                return {**metadata, "window_id": payload["window_id"], **result}
            if operation == "desktop.screenshot":
                capture = self.backend.capture(payload)
                # Capture must not validate input against pre-capture layout/window geometry.
                if self.backend.layout()["layout_revision"] != metadata["layout_revision"]:
                    self.abort()
                    raise RACPError(
                        "STALE_OBSERVATION", "layout changed during capture", layer="broker"
                    )
                buffers = []
                try:
                    for key, destination in (
                        ("_png", capture),
                        ("_preview_png", capture.get("preview", {})),
                    ):
                        raw = capture.pop(key, None)
                        if raw is not None:
                            descriptor = self.captures.put(
                                raw, context.owner_id, context.device_id, context.operation_id
                            )
                            buffers.append(descriptor["capture_id"])
                            destination["capture"] = descriptor
                except BaseException:
                    for identifier in buffers:
                        self.captures.blobs.pop(identifier, None)
                    raise
                return {**metadata, **capture}
            return metadata
        lease = self.guard(payload, context, activate=operation == "desktop.activate")
        try:
            result = self.backend.action(
                operation,
                payload,
                lambda: self.guard(payload, context, activate=operation == "desktop.activate"),
            )
            # SendInput dispatch is not an application-level assertion of accepted input.
            if operation == "desktop.activate":
                self.available()
                if (
                    self.backend.input_tick() != lease.input_tick
                    or self.backend.foreground() != payload["expected_window_id"]
                ):
                    raise RACPError(
                        "STALE_OBSERVATION",
                        "input or focus changed during activation",
                        layer="broker",
                        execution_state="unknown",
                    )
                lease.foreground = payload["expected_window_id"]
                lease.foreground_tick = self.backend.foreground_tick()
            else:
                try:
                    self.check_lease()
                except RACPError as exc:
                    raise RACPError(
                        exc.error.code,
                        "desktop changed during input dispatch",
                        layer="broker",
                        execution_state="unknown",
                    ) from exc
            self.observations.clear()
            self.backend.clear_observations()
            return {
                **result,
                "verification": "os_dispatch_only",
                "fresh_observation_required": True,
            }
        except BaseException:
            self.abort()
            raise
        finally:
            self.backend.release_inputs()

    def handle(self, raw: dict[str, Any]) -> dict[str, Any]:
        try:
            request = BrokerRequest.model_validate(raw)
            result = self.execute(request.operation, request.payload, request.context)
            return {"state": "SUCCEEDED", "result": result, "error": None}
        except ValidationError:
            return {
                "state": "FAILED",
                "result": None,
                "error": asdict(
                    RACPError("INVALID_ARGUMENT", "invalid Broker request", layer="broker").error
                ),
            }
        except RACPError as exc:
            return {"state": "FAILED", "result": None, "error": asdict(exc.error)}
        except Exception:
            try:
                self.abort()
            except Exception:
                pass
            return {
                "state": "FAILED",
                "result": None,
                "error": asdict(
                    RACPError(
                        "BROKER_FAILURE",
                        "native desktop operation failed",
                        layer="broker",
                        execution_state="unknown",
                        cleanup_status="unverified",
                    ).error
                ),
            }
