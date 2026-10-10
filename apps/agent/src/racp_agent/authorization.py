"""Authoritative local gates and output redaction, independent of Gateway policy."""

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from racp_domain.models import RACPError
from racp_policy.engine import Decision
from racp_policy.permissions import PermissionSnapshot
from racp_protocol.models import Request


@dataclass(frozen=True)
class WorkspaceAuthority:
    workspace_id: str
    roots: tuple[tuple[str, str], ...]


class WorkspaceBindings:
    """Agent-derived scope proofs; these fields never come from an RPC payload."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS execution_workspace_bindings (
            operation_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, device_id TEXT NOT NULL,
            recorded_boot_id TEXT NOT NULL, handle_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            roots TEXT NOT NULL)""")

    def bind(self, request: Request, workspace_id: str, roots: dict[str, str]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO execution_workspace_bindings VALUES (?,?,?,?,?,?,?)",
            (
                request.operation_id,
                request.context.principal_id,
                request.device_id,
                request.agent_boot_id,
                request.payload.get("handle_id", ""),
                workspace_id,
                json.dumps(roots, sort_keys=True),
            ),
        )

    def resolve(self, request: Request) -> WorkspaceAuthority:
        row = self.db.execute(
            "SELECT workspace_id,roots FROM execution_workspace_bindings WHERE "
            "operation_id=? AND principal_id=? AND device_id=? "
            "AND recorded_boot_id=? AND handle_id=?",
            (
                request.operation_id,
                request.context.principal_id,
                request.device_id,
                request.agent_boot_id,
                request.payload.get("handle_id", ""),
            ),
        ).fetchone()
        if row is None:
            raise RACPError(
                "PERMISSION_DENIED",
                "Retained output has no verified workspace binding",
                layer="agent",
                reason="OUTPUT_SCOPE_UNVERIFIED",
            )
        try:
            roots = json.loads(row[1])
        except (ValueError, TypeError):
            raise RACPError(
                "PERMISSION_DENIED", "Retained workspace binding is invalid", layer="agent"
            ) from None
        if (
            not isinstance(roots, dict)
            or not 1 <= len(roots) <= 16
            or row[0] not in roots
            or not all(
                isinstance(key, str)
                and isinstance(value, str)
                and re.fullmatch(r"[0-9a-f]{64}", value)
                for key, value in roots.items()
            )
        ):
            raise RACPError(
                "PERMISSION_DENIED", "Retained workspace binding is invalid", layer="agent"
            )
        return WorkspaceAuthority(row[0], tuple(sorted(roots.items())))

    def collect(self) -> int:
        """Retire at most one maintenance batch of proofs with no deliverable outcome."""
        cursor = self.db.execute(
            "DELETE FROM execution_workspace_bindings WHERE operation_id IN ("
            "SELECT binding.operation_id FROM execution_workspace_bindings AS binding "
            "WHERE NOT EXISTS (SELECT 1 FROM operations AS operation "
            "WHERE operation.id=binding.operation_id AND operation.outcome_available=1) "
            "ORDER BY binding.rowid LIMIT 1000)"
        )
        return cursor.rowcount


def authorize(
    snapshot: PermissionSnapshot,
    request: Request,
    *,
    owned: bool = False,
    completed: bool = False,
    workspace_id: str | None = None,
) -> None:
    decision = snapshot.decision(
        request.operation,
        request.payload,
        workspace_id=workspace_id or request.context.workspace_id,
        timeout_ms=request.timeout_ms,
        owned=owned,
    )
    if decision != Decision.ALLOW:
        raise RACPError(
            "APPROVAL_REQUIRED" if decision == Decision.REQUIRE_APPROVAL else "PERMISSION_DENIED",
            "Client local permission ceiling blocks this operation",
            layer="agent",
            execution_state="completed" if completed else "not_started",
            permission_revision=snapshot.revision,
            reason="LOCAL_APPROVAL_UNAVAILABLE"
            if decision == Decision.REQUIRE_APPROVAL
            else "LOCAL_PERMISSION_DENIED",
        )


def redact_arguments(value: Any) -> Any:
    """Copy output trees: Provider snapshot caches must not be modified by redaction."""
    if isinstance(value, list):
        return [redact_arguments(item) for item in value]
    if isinstance(value, dict):
        result = {key: redact_arguments(item) for key, item in value.items()}
        if "cmdline" in result:
            result["cmdline"] = None
            result["arguments_redacted"] = True
        return result
    return value


def contains_attachment(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (key.endswith("artifact_id") and bool(item)) or contains_attachment(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_attachment(item) for item in value)
    return False
