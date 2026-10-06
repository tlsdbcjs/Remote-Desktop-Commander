"""Bounded local event summaries; never expose raw logs, payloads or credentials."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from racp_domain.models import OperationState
from racp_protocol.registry import REGISTRY

from racp_agent.background import paths
from racp_agent.settings import local_path

EVENTS = frozenset(
    {
        "agent_connected",
        "agent_disconnected",
        "agent_handshake_rejected",
        "agent_execution_started",
        "agent_result",
    }
)


def activity(credentials: Path) -> dict[str, Any]:
    root, _ = paths(credentials)
    log = local_path(root / "agent.log")
    try:
        with log.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            offset = max(0, size - 32768)
            stream.seek(offset)
            raw = stream.read(32768)
    except FileNotFoundError:
        return {"events": [], "tail_limited": False}
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if offset:
        lines = lines[1:]
    events = []
    for line in lines:
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("event"), str)
            or value["event"] not in EVENTS
        ):
            continue
        event: dict[str, Any] = {"event": value["event"]}
        stamp = value.get("timestamp")
        if isinstance(stamp, str) and len(stamp) <= 40:
            try:
                event["timestamp"] = datetime.fromisoformat(stamp).isoformat()
            except ValueError:
                pass
        if isinstance(value.get("operation"), str) and value["operation"] in REGISTRY:
            event["operation"] = value["operation"]
        if isinstance(value.get("state"), str) and value["state"] in {
            item.value for item in OperationState
        }:
            event["state"] = value["state"]
        identifier = value.get("operation_id")
        if isinstance(identifier, str) and re.fullmatch(r"op_[0-9a-f]{32}", identifier):
            event["operation_id"] = identifier
        events.append(event)
    return {"events": events[-40:], "tail_limited": bool(offset or len(events) > 40)}
