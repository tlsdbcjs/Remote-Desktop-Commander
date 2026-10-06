"""Bounded UI Automation observations; original COM references never relocate by selector."""

import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import new_id

from racp_agent.broker.identity import Identity, process_identity

windows_ctypes: Any = ctypes


@dataclass
class ElementReference:
    element: Any
    root: Any
    fingerprint: dict[str, Any]
    window_id: str
    expires: float


class Automation:
    def __init__(self, identity: Identity) -> None:
        self.identity = identity
        self.references: dict[str, dict[str, ElementReference]] = {}
        # The Broker RPC thread owns no HWNDs. All COM pointers stay on this MTA thread.
        previous = getattr(sys, "coinit_flags", None)
        sys.coinit_flags = 0  # type: ignore[attr-defined]
        try:
            import comtypes
            import comtypes.client
        finally:
            if previous is None:
                del sys.coinit_flags  # type: ignore[attr-defined]
            else:
                sys.coinit_flags = previous  # type: ignore[attr-defined]
        self.com: Any = comtypes
        self.com.CoInitializeEx(0)
        try:
            client: Any = comtypes.client
            client.gen_dir = None  # OS type library wrappers live in memory, never shared files.
            system = windows_ctypes.WinDLL("kernel32", use_last_error=True)
            system.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
            directory = ctypes.create_unicode_buffer(32768)
            length = system.GetSystemDirectoryW(directory, len(directory))
            if not 0 < length < len(directory):
                raise OSError("system directory unavailable")
            self.module: Any = client.GetModule(
                os.path.join(directory.value, "UIAutomationCore.dll")
            )
            self.client: Any = client.CreateObject(
                self.module.CUIAutomation8, interface=self.module.IUIAutomation2
            )
            self.client.ConnectionTimeout = 500
            self.client.TransactionTimeout = 500
            self.client.AutoSetFocus = False
            self.walker: Any = self.client.ControlViewWalker
        except self.com.COMError as exc:
            self.com.CoUninitialize()
            raise RACPError(
                "CAPABILITY_UNAVAILABLE", "UI Automation initialization failed", layer="broker"
            ) from exc
        except BaseException:
            self.com.CoUninitialize()
            raise

    def close(self) -> None:
        self.references.clear()
        self.walker = self.client = None
        self.com.CoUninitialize()

    def clear(self) -> None:
        self.references.clear()

    def fingerprint(self, element: Any) -> dict[str, Any]:
        rectangle = element.CurrentBoundingRectangle
        pid, created = int(element.CurrentProcessId), None
        if pid > 0:
            try:
                created = process_identity(pid).created
            except Exception:
                pass  # Inaccessible identities remain observable, never actionable.
        return {
            "runtime_id": tuple(element.GetRuntimeId()),
            "pid": pid,
            "process_created": created,
            "bounds": [rectangle.left, rectangle.top, rectangle.right, rectangle.bottom],
            "control_type": int(element.CurrentControlType),
            "automation_id": str(element.CurrentAutomationId)[:128],
            "name": "" if element.CurrentIsPassword else str(element.CurrentName)[:256],
            "enabled": bool(element.CurrentIsEnabled),
            "offscreen": bool(element.CurrentIsOffscreen),
            "password": bool(element.CurrentIsPassword),
        }

    def pattern(self, element: Any, name: str) -> Any:
        identifier = getattr(self.module, "UIA_" + name + "PatternId")
        interface = getattr(self.module, "IUIAutomation" + name + "Pattern")
        return element.GetCurrentPattern(identifier).QueryInterface(interface)

    def inspect(self, handle: int, window_id: str, observation: str, limit: int) -> dict[str, Any]:
        now = time.monotonic()
        self.references = {
            key: refs
            for key, refs in self.references.items()
            if refs and next(iter(refs.values())).expires > now
        }
        if sum(len(refs) for refs in self.references.values()) + limit > 256:
            raise RACPError("RESOURCE_EXHAUSTED", "UI Automation reference limit", layer="broker")
        root = self.client.ElementFromHandle(handle)
        references: dict[str, ElementReference] = {}
        nodes: list[dict[str, Any]] = []
        stack: list[tuple[Any, str | None, int]] = [(root, None, 0)]
        deadline, size, truncated = now + 1, 0, False
        try:
            while stack and len(nodes) < limit and time.monotonic() < deadline:
                element, parent, depth = stack.pop()
                fingerprint = self.fingerprint(element)
                reference = new_id("element")
                patterns = []
                if fingerprint["enabled"] and not fingerprint["password"]:
                    for name in ("Invoke", "Value"):
                        try:
                            if self.pattern(element, name):
                                patterns.append(name.lower())
                        except (self.com.COMError, ValueError):
                            pass
                node = {
                    "element_ref": reference,
                    "parent_ref": parent,
                    "depth": depth,
                    **{key: value for key, value in fingerprint.items() if key != "runtime_id"},
                    "patterns": patterns,
                }
                encoded_size = len(json.dumps(node, ensure_ascii=False).encode("utf-8"))
                if size + encoded_size > 32768:
                    truncated = True
                    break
                size += encoded_size
                nodes.append(node)
                references[reference] = ElementReference(
                    element, root, fingerprint, window_id, now + 5
                )
                if depth:
                    sibling = self.walker.GetNextSiblingElement(element)
                    if sibling:
                        stack.append((sibling, parent, depth))
                if depth < 8:
                    child = self.walker.GetFirstChildElement(element)
                    if child:
                        stack.append((child, reference, depth + 1))
                else:
                    truncated = True
            truncated = truncated or bool(stack) or time.monotonic() >= deadline
        except (self.com.COMError, ValueError) as exc:
            raise RACPError(
                "STALE_OBSERVATION", "UI Automation tree changed", layer="broker"
            ) from exc
        self.references[observation] = references
        return {"elements": nodes, "truncated": truncated, "backend": "ui_automation"}

    def action(self, operation: str, payload: dict[str, Any], guard: Any) -> dict[str, Any]:
        reference = self.references.get(payload["observation_id"], {}).get(payload["element_ref"])
        if (
            reference is None
            or reference.expires <= time.monotonic()
            or reference.window_id != payload["expected_window_id"]
        ):
            raise RACPError("STALE_OBSERVATION", "UI Automation reference expired", layer="broker")
        dispatched = False
        try:
            guard()
            element = reference.element
            current = self.fingerprint(element)
            if current != reference.fingerprint or not current["enabled"] or current["offscreen"]:
                raise RACPError(
                    "STALE_OBSERVATION", "UI Automation element changed", layer="broker"
                )
            if current["password"]:
                raise RACPError(
                    "PERMISSION_DENIED", "password element cannot be automated", layer="broker"
                )
            if current["process_created"] is None:
                raise RACPError(
                    "PERMISSION_DENIED", "UI element process identity unavailable", layer="broker"
                )
            identity = process_identity(current["pid"])
            if (
                identity.session != self.identity.session
                or identity.sid != self.identity.sid
                or identity.integrity > self.identity.integrity
            ):
                raise RACPError(
                    "INTEGRITY_LEVEL_MISMATCH", "UI element identity mismatch", layer="broker"
                )
            ancestor, contained = element, False
            for _ in range(16):
                if self.client.CompareElements(ancestor, reference.root):
                    contained = True
                    break
                ancestor = self.walker.GetParentElement(ancestor)
                if not ancestor:
                    break
            if not contained:
                raise RACPError(
                    "STALE_OBSERVATION", "UI element left the observed window", layer="broker"
                )
            name = "Invoke" if operation == "desktop.invoke" else "Value"
            try:
                pattern = self.pattern(element, name)
            except (self.com.COMError, ValueError) as exc:
                raise RACPError(
                    "CAPABILITY_UNAVAILABLE", "UI pattern unavailable", layer="broker"
                ) from exc
            if name == "Value" and pattern.CurrentIsReadOnly:
                raise RACPError("PERMISSION_DENIED", "UI value is read-only", layer="broker")
            if self.fingerprint(element) != reference.fingerprint:
                raise RACPError(
                    "STALE_OBSERVATION", "UI element changed before dispatch", layer="broker"
                )
            guard()
            dispatched = True
            if name == "Invoke":
                pattern.Invoke()
            else:
                pattern.SetValue(payload["value"])
            return {"dispatched": True, "backend": "ui_automation", "pattern": name.lower()}
        except (self.com.COMError, ValueError) as exc:
            raise RACPError(
                "INPUT_DISPATCH_FAILED" if dispatched else "STALE_OBSERVATION",
                "UI Automation provider rejected the request",
                layer="broker",
                execution_state="unknown" if dispatched else "not_started",
            ) from exc
