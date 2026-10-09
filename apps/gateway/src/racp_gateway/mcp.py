import base64
import inspect
from dataclasses import asdict
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent
from pydantic import AnyHttpUrl, ValidationError
from racp_domain.models import RACPError
from racp_domain.version import VERSION
from racp_protocol.models import OperationInput
from racp_protocol.registry import INPUT_MODELS, REGISTRY, OperationSpec

from racp_gateway.oauth import OAuthTokenVerifier, OAuthToolScopes
from racp_gateway.service import ControlPlane


def create_mcp(control: ControlPlane, oauth: OAuthTokenVerifier | None = None) -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer(
        "RACP",
        version=VERSION,
        token_verifier=oauth,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(oauth.config.issuer),
            resource_server_url=AnyHttpUrl(oauth.resource),
            required_scopes=[oauth.config.read_scope],
        )
        if oauth
        else None,
        middleware=[OAuthToolScopes(oauth)] if oauth else None,
    )

    async def result_of(action: Any) -> CallToolResult:
        try:
            value = await action
            content: list[ContentBlock] = [TextContent(type="text", text=str(value))]
            result = value.get("result") or {}
            identifier = value.get("operation_id")
            if value.get("state") == "SUCCEEDED" and identifier and control.artifacts is not None:
                record = control.store.get(identifier)
                if record["request"]["operation"] in {"desktop.screenshot", "browser.screenshot"}:
                    preview = result.get("preview") or result
                    artifact_id = preview.get("artifact_id")
                    if artifact_id:
                        try:
                            data = await control.artifacts.png_preview(
                                artifact_id, "owner_local", identifier
                            )
                            if data is not None:
                                content.append(
                                    ImageContent(
                                        type="image",
                                        data=base64.b64encode(data).decode("ascii"),
                                        mime_type="image/png",
                                    )
                                )
                        except (RACPError, OSError):
                            content.append(
                                TextContent(
                                    type="text",
                                    text="Image preview unavailable; inspect output attachments.",
                                )
                            )
            return CallToolResult(
                content=content,
                structured_content=value,
                is_error=bool(value.get("error"))
                or value.get("state") in {"FAILED", "TIMED_OUT", "CANCELLED", "UNKNOWN"},
            )
        except RACPError as exc:
            error = asdict(exc.error)
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=exc.error.message)],
                structured_content={"error": error},
            )
        except ValidationError:
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text="invalid input")],
                structured_content={
                    "error": asdict(RACPError("INVALID_ARGUMENT", "invalid input").error)
                },
            )

    @server.tool()
    async def device_list() -> dict[str, Any]:
        """List owned PCs and capabilities. Select a device_id explicitly.

        Filesystem attributes.workspaces lists locally approved workspace IDs and paths.
        Use workspace_id on file and execution tools; default selects the primary folder.
        """
        return {"items": control.store.devices("owner_local"), "next_cursor": None}

    @server.tool()
    async def shell_exec(
        device_id: str,
        idempotency_key: str,
        argv: list[str] | None = None,
        mode: Literal["argv", "shell"] = "argv",
        command: str | None = None,
        shell: Literal["powershell", "pwsh", "cmd", "bash"] | None = None,
        cwd: str | None = None,
        env: dict[str, str | None] | None = None,
        encoding: str = "utf-8",
        timeout_ms: int | None = None,
        execution_mode: Literal["sync", "job"] = "sync",
        execution_profile_id: Literal["read_only", "standard", "trusted_personal"] = "read_only",
        approval_id: str | None = None,
        workspace_id: str = "default",
    ) -> CallToolResult:
        """Execute a command on the selected PC, requiring a unique key and allowed policy.

        Choose workspace_id from device_list. Relative cwd starts in that approved folder.
        Commands run with the PC's OS account permissions; cwd is not a filesystem sandbox.
        """

        async def invoke() -> dict[str, Any]:
            if execution_mode == "sync" and timeout_ms is not None and timeout_ms > 20000:
                raise RACPError(
                    "INVALID_ARGUMENT",
                    "MCP sync budget is at most 20 seconds; select job before execution",
                )
            return await control.execute(
                OperationInput(
                    device_id=device_id,
                    workspace_id=workspace_id,
                    operation="shell.exec",
                    idempotency_key=idempotency_key,
                    payload={
                        "mode": mode,
                        "argv": argv,
                        "command": command,
                        "shell": shell,
                        "cwd": cwd,
                        "env": env or {},
                        "encoding": encoding,
                    },
                    timeout_ms=(
                        timeout_ms if execution_mode == "job" else min(timeout_ms or 20000, 20000)
                    ),
                    execution_mode=execution_mode,
                    execution_profile_id=execution_profile_id,
                    approval_id=approval_id,
                ),
                "owner_local",
            )

        return await result_of(invoke())

    @server.tool()
    async def operation_get(operation_id: str) -> CallToolResult:
        """Inspect an existing execution without re-running it."""

        async def invoke() -> dict[str, Any]:
            return control.get(operation_id, "owner_local")

        return await result_of(invoke())

    @server.tool()
    async def operation_cancel(operation_id: str) -> CallToolResult:
        """Request cancellation; CANCEL_REQUESTED is distinct from confirmed termination."""
        return await result_of(control.cancel(operation_id, "owner_local"))

    @server.tool()
    async def operation_resolutions(operation_id: str) -> CallToolResult:
        """Inspect late reported results while the original UNKNOWN state remains immutable."""

        async def invoke() -> dict[str, Any]:
            return control.resolutions(operation_id, "owner_local")

        return await result_of(invoke())

    @server.tool()
    async def operation_outputs(operation_id: str) -> CallToolResult:
        """Inspect output attachments recovered after the execution outcome was committed."""

        async def invoke() -> dict[str, Any]:
            return control.outputs(operation_id, "owner_local")

        return await result_of(invoke())

    @server.tool()
    async def job_get(job_id: str) -> CallToolResult:
        """Poll a durable Job without re-running it."""

        async def invoke() -> dict[str, Any]:
            return control.jobs.get(job_id, "owner_local")

        return await result_of(invoke())

    @server.tool()
    async def job_cancel(job_id: str) -> CallToolResult:
        """Cancel a long-running shell job."""
        return await result_of(control.cancel_job(job_id, "owner_local"))

    @server.tool()
    async def job_list(
        device_id: str | None = None, limit: int = 100, cursor: str | None = None
    ) -> CallToolResult:
        """List owned Jobs with bounded pagination."""

        async def invoke() -> dict[str, Any]:
            if not 1 <= limit <= 500:
                raise RACPError("INVALID_ARGUMENT", "invalid job page limit")
            return control.jobs.list("owner_local", device=device_id, limit=limit, cursor=cursor)

        return await result_of(invoke())

    def provider_tool(spec: OperationSpec) -> None:
        async def invoke(**kwargs: Any) -> CallToolResult:
            async def execute() -> dict[str, Any]:
                headers = {
                    name: kwargs.pop(name)
                    for name in (
                        "device_id",
                        "workspace_id",
                        "idempotency_key",
                        "execution_profile_id",
                        "timeout_ms",
                        "execution_mode",
                        "approval_id",
                    )
                    if name in kwargs
                }
                if headers.get("execution_mode", "sync") == "sync":
                    if headers.get("timeout_ms") is not None and headers["timeout_ms"] > 20000:
                        raise RACPError(
                            "INVALID_ARGUMENT",
                            "MCP sync budget is at most 20 seconds; select job before execution",
                        )
                    headers["timeout_ms"] = min(
                        headers.get("timeout_ms") or spec.default_timeout_ms, 20000
                    )
                payload = (
                    kwargs.pop("payload")
                    if getattr(model, "__pydantic_root_model__", False)
                    else kwargs
                )
                if hasattr(payload, "model_dump"):
                    payload = payload.model_dump()
                return await control.execute(
                    OperationInput(operation=spec.name, payload=payload, **headers), "owner_local"
                )

            return await result_of(execute())

        parameters = [
            inspect.Parameter("device_id", inspect.Parameter.KEYWORD_ONLY, annotation=str)
        ]
        model = INPUT_MODELS[spec.name]
        for name, field in model.model_fields.items():
            if getattr(model, "__pydantic_root_model__", False):
                parameters.append(
                    inspect.Parameter("payload", inspect.Parameter.KEYWORD_ONLY, annotation=model)
                )
                break
            default = (
                inspect.Parameter.empty
                if field.is_required()
                else field.get_default(call_default_factory=True)
            )
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=field.annotation,
                    default=default,
                )
            )
        parameters.extend(
            [
                inspect.Parameter(
                    "idempotency_key",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str if spec.side_effect else str | None,
                    default=inspect.Parameter.empty if spec.side_effect else None,
                ),
                inspect.Parameter(
                    "execution_profile_id",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Literal["read_only", "standard", "trusted_personal"],
                    default="read_only",
                ),
                inspect.Parameter(
                    "timeout_ms",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=int | None,
                    default=None,
                ),
                inspect.Parameter(
                    "execution_mode",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Literal["sync", "job"],
                    default="sync",
                ),
                inspect.Parameter(
                    "approval_id",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str | None,
                    default=None,
                ),
                inspect.Parameter(
                    "workspace_id",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=str,
                    default="default",
                ),
            ]
        )
        invoke.__name__ = spec.mcp_name
        invoke.__signature__ = inspect.Signature(parameters, return_annotation=CallToolResult)  # type: ignore[attr-defined]
        description = f"{spec.name}: authenticated operation with journaled mutations."
        if spec.capability in {"filesystem", "process", "terminal", "re", "debugger"}:
            description += (
                " Choose workspace_id from device_list; paths and initial cwd are relative"
                " to that folder. Existing handles retain their original execution directory."
            )
        if spec.name in {"filesystem.copy", "filesystem.move"}:
            description += " Select destination_workspace_id explicitly to use another folder."
        server.add_tool(
            invoke,
            name=spec.mcp_name,
            description=description,
        )

    for spec in REGISTRY.values():
        if spec.name != "shell.exec":
            provider_tool(spec)

    return server
