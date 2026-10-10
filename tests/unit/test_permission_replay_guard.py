"""Permission changes at restart must also govern durable outcomes and descriptors."""

import asyncio
import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from racp_agent.authorization import WorkspaceAuthority, WorkspaceBindings
from racp_agent.runtime import Agent
from racp_domain.models import RACPError
from racp_policy.permissions import LocalPermissions, compile_permissions
from racp_protocol.models import Context, OutputDescriptor, Reconcile, Request
from racp_sdk.journal import Journal
from racp_sdk.peer_writer import PeerWriter


def retained(operation: str, payload: dict, result: dict) -> tuple[Agent, dict]:
    agent = Agent.__new__(Agent)
    agent.device_id, agent.boot_id, agent.epoch = "dev_fixture", "boot_restarted", 2
    agent.profile = "trusted_personal"
    agent.terminals = SimpleNamespace(sessions={})
    agent.processes = SimpleNamespace(managed={})
    agent.permissions = compile_permissions(LocalPermissions())
    agent.outputs = SimpleNamespace(descriptors=lambda identifier: [])
    agent.workspace_bindings = SimpleNamespace(
        resolve=lambda request: WorkspaceAuthority(
            request.context.workspace_id,
            ((request.context.workspace_id, "root-" + request.context.workspace_id),),
        )
    )
    agent.workspace_fingerprint = lambda identity: "root-" + identity
    request = Request(
        device_id=agent.device_id,
        agent_boot_id="boot_previous",
        connection_epoch=1,
        request_id="req_retained",
        operation_id="op_retained",
        trace_id="0" * 32,
        timestamp=datetime.now(UTC).isoformat(),
        operation=operation,
        timeout_ms=1000,
        remaining_timeout_ms=1000,
        execution_mode="sync",
        idempotency_key="retained-key",
        context=Context(principal_id="owner", execution_profile_id="read_only", policy_revision=1),
        payload=payload,
    )
    record = {
        "id": request.operation_id,
        "request": request.model_dump(),
        "state": "SUCCEEDED",
        "result": result,
        "error": None,
        "outcome_available": True,
    }
    return agent, record


def test_durable_text_is_withheld_without_modifying_execution_fact() -> None:
    agent, record = retained(
        "filesystem.read", {"path": "read.txt"}, {"text": "PRIVATE_PREVIOUS_CEILING"}
    )
    original = copy.deepcopy(record)
    wire = agent.result_message(record)
    assert "PRIVATE_PREVIOUS_CEILING" not in wire.model_dump_json()
    assert wire.error and wire.error["code"] == "PERMISSION_DENIED"
    assert record == original and record["state"] == "SUCCEEDED"


def test_durable_process_arguments_are_redacted_under_current_leaf() -> None:
    agent, record = retained(
        "process.inspect", {"pid": 123}, {"pid": 123, "cmdline": ["PRIVATE_ARG"]}
    )
    agent.permissions = compile_permissions(LocalPermissions(grants={"process.inspect": "allow"}))
    wire = agent.result_message(record)
    assert wire.result["cmdline"] is None and wire.result["arguments_redacted"]
    assert record["result"]["cmdline"] == ["PRIVATE_ARG"]


def test_durable_artifact_descriptors_are_withheld_when_export_is_off() -> None:
    agent, record = retained(
        "filesystem.read", {"path": "read.txt"}, {"text": "inline", "artifact_id": "art_private"}
    )
    agent.permissions = compile_permissions(LocalPermissions(grants={"files.read.text": "allow"}))
    agent.outputs = SimpleNamespace(
        descriptors=lambda identifier: [
            OutputDescriptor.model_validate(
                {
                    "id": "output_private",
                    "size_bytes": 3,
                    "sha256": "a" * 64,
                    "media_type": "text/plain",
                    "artifact_id": "art_private",
                }
            )
        ]
    )
    wire = agent.result_message(record)
    assert wire.outputs == [] and "art_private" not in wire.model_dump_json()


def test_retained_shell_output_obeys_narrowed_local_profile() -> None:
    agent, record = retained(
        "shell.exec", {"argv": ["fixture.exe"]}, {"stdout": "PRIVATE_OLD_SHELL"}
    )
    record["request"]["context"]["execution_profile_id"] = "trusted_personal"
    agent.profile = "read_only"
    agent.permissions = compile_permissions(LocalPermissions(grants={"exec.argv": "allow"}))
    wire = agent.result_message(record)
    assert "PRIVATE_OLD_SHELL" not in wire.model_dump_json()
    assert wire.error["code"] == "PERMISSION_DENIED"


def test_cold_terminal_replay_uses_bound_workspace_not_callers_default() -> None:
    agent, record = retained(
        "terminal.read", {"handle_id": "term_old"}, {"data": "PRIVATE_ANALYSIS_TERMINAL"}
    )
    agent.workspace_bindings = SimpleNamespace(
        resolve=lambda request: WorkspaceAuthority("analysis", (("analysis", "root-analysis"),))
    )
    agent.permissions = compile_permissions(
        LocalPermissions.model_validate_json(
            '{"grants":{"terminal.output.read":"allow"},"constraints":{"workspace_ids":["default"]}}'
        )
    )
    wire = agent.result_message(record)
    assert wire.result is None and wire.error["code"] == "PERMISSION_DENIED"
    assert "PRIVATE_ANALYSIS_TERMINAL" not in wire.model_dump_json()


def test_changed_root_for_same_workspace_id_withholds_retained_file_bytes() -> None:
    agent, record = retained("filesystem.read", {"path": "read.txt"}, {"text": "PRIVATE_OLD_ROOT"})
    agent.permissions = compile_permissions(LocalPermissions(grants={"files.read.text": "allow"}))
    agent.workspace_fingerprint = lambda identity: "root-replaced"
    wire = agent.result_message(record)
    assert wire.result is None and wire.error["code"] == "PERMISSION_DENIED"
    assert "PRIVATE_OLD_ROOT" not in wire.model_dump_json()


def test_workspace_binding_survives_restart_and_rejects_other_owner_or_boot(tmp_path: Path) -> None:
    _, record = retained("terminal.read", {"handle_id": "term_old"}, {"data": "owned"})
    request = Request.model_validate(record["request"])
    path = tmp_path / "binding.db"
    with sqlite3.connect(path) as db:
        WorkspaceBindings(db).bind(request, "analysis", {"analysis": "a" * 64})
    db.close()
    with sqlite3.connect(path) as db:
        bindings = WorkspaceBindings(db)
        assert bindings.resolve(request).workspace_id == "analysis"
        other = request.model_copy(
            update={"context": request.context.model_copy(update={"principal_id": "other"})}
        )
        for invalid in (other, request.model_copy(update={"agent_boot_id": "boot_other"})):
            with pytest.raises(RACPError):
                bindings.resolve(invalid)
        db.execute("UPDATE execution_workspace_bindings SET roots='{}'")
        with pytest.raises(RACPError):
            bindings.resolve(request)
    db.close()


async def test_queued_reconcile_rechecks_nested_records_at_transmission() -> None:
    agent, record = retained(
        "filesystem.read", {"path": "read.txt"}, {"text": "PRIVATE_PENDING_RECONCILE"}
    )
    agent.permissions = compile_permissions(LocalPermissions(grants={"files.read.text": "allow"}))
    agent.journal = SimpleNamespace(get=lambda identifier: record)
    entered, release = asyncio.Event(), asyncio.Event()
    sent: list[dict] = []

    async def socket_send(raw: str) -> None:
        if not sent:
            entered.set()
            await release.wait()
        sent.append(json.loads(raw))

    agent.writer = PeerWriter(socket_send)
    control = asyncio.create_task(
        agent.send(
            SimpleNamespace(type="heartbeat", model_dump_json=lambda: '{"type":"heartbeat"}')
        )
    )
    queued = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        envelope = Reconcile(
            device_id=agent.device_id,
            agent_boot_id=agent.boot_id,
            connection_epoch=agent.epoch,
            records=[agent.result_message(record)],
            complete=False,
        )
        queued = asyncio.create_task(agent.send(envelope))
        await asyncio.sleep(0)
        agent.permissions = compile_permissions(LocalPermissions())
        release.set()
        await asyncio.wait_for(control, 2)
        await asyncio.wait_for(queued, 2)
        assert "PRIVATE_PENDING_RECONCILE" not in json.dumps(sent)
        assert sent[1]["records"][0]["error"]["code"] == "PERMISSION_DENIED"
        assert record["result"]["text"] == "PRIVATE_PENDING_RECONCILE"
    finally:
        release.set()
        for task in (control, queued):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *[task for task in (control, queued) if task is not None], return_exceptions=True
        )
        await agent.writer.close()


def test_agent_maintenance_retires_scope_proofs_but_preserves_live_and_pinned(
    tmp_path: Path,
) -> None:
    agent = Agent.__new__(Agent)
    agent.journal = Journal(tmp_path / "retention.db")
    agent.workspace_bindings = WorkspaceBindings(agent.journal.db)
    agent.tasks = {"op_task": object()}
    agent.outputs = SimpleNamespace(pinned_operations=lambda: {"op_upload"})
    try:
        for identity in ("transient", "expired", "active", "task", "upload"):
            _, record = retained("filesystem.stat", {"path": "fixture"}, {"exists": True})
            request = Request.model_validate(record["request"]).model_copy(
                update={"operation_id": "op_" + identity}
            )
            agent.journal.accept(
                "scope",
                identity,
                identity,
                request.model_dump(),
                retain_key=identity != "transient",
            )
            agent.workspace_bindings.bind(request, "default", {"default": "a" * 64})
            if identity != "active":
                agent.journal.transition(request.operation_id, "SUCCEEDED", result={"exists": True})
        agent.journal.db.execute(
            "UPDATE operations SET updated_at=?",
            ((datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z"),),
        )
        agent.journal.db.execute(
            "UPDATE outcome_retention_clocks SET committed_monotonic=committed_monotonic-172800"
        )
        agent.compact_outcomes()
        assert not agent.journal.get("op_expired")["outcome_available"]
        assert agent.journal.get("op_task")["outcome_available"]
        remaining = {
            row[0]
            for row in agent.journal.db.execute(
                "SELECT operation_id FROM execution_workspace_bindings"
            )
        }
        assert remaining == {"op_active", "op_task", "op_upload"}
    finally:
        agent.journal.close()


def test_scope_proof_collection_is_bounded_and_eventually_reclaims_orphans(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "orphan.db")
    try:
        bindings = WorkspaceBindings(journal.db)
        _, record = retained("filesystem.stat", {"path": "fixture"}, {"exists": True})
        request = Request.model_validate(record["request"])
        for index in range(1001):
            bindings.bind(
                request.model_copy(update={"operation_id": f"op_{index}"}),
                "default",
                {"default": "a" * 64},
            )
        assert bindings.collect() == 1000
        assert (
            journal.db.execute("SELECT COUNT(*) FROM execution_workspace_bindings").fetchone()[0]
            == 1
        )
        assert bindings.collect() == 1
        assert bindings.collect() == 0
    finally:
        journal.close()
