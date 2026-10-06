"""Versioned local plugin contract; never a dynamic public operation registry."""

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, TypeAdapter, model_validator

from racp_protocol.models import Identifier, StrictModel

SemanticVersion = Annotated[
    str,
    Field(pattern=r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$", max_length=64),
]
Permission = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]+$", max_length=128)]


def exact_version(value: Any) -> Literal[1]:
    if type(value) is not int or value != 1:
        raise ValueError("plugin protocol version must be integer 1")
    return 1


ProtocolVersion = Annotated[Literal[1], BeforeValidator(exact_version)]


class PluginOperation(StrictModel):
    name: str = Field(pattern=r"^(re|debugger|external_mcp)\.[a-z][a-z0-9_]*$", max_length=128)
    capability: Literal["static-analysis", "debugger", "external-mcp"]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission_scope: Permission
    side_effect: bool
    execution_modes: list[Literal["sync", "job"]] = Field(min_length=1, max_length=2)
    max_timeout_ms: int = Field(default=30000, ge=100, le=86400000)

    @model_validator(mode="after")
    def namespace(self) -> "PluginOperation":
        expected = {"re": "static-analysis", "debugger": "debugger", "external_mcp": "external-mcp"}
        if expected[self.name.split(".")[0]] != self.capability:
            raise ValueError("plugin operation namespace/capability mismatch")
        if len(set(self.execution_modes)) != len(self.execution_modes):
            raise ValueError("duplicate plugin execution mode")
        return self


class PluginManifest(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=64)
    version: SemanticVersion
    protocol_version: ProtocolVersion = 1
    transport: Literal["stdio"] = "stdio"
    backend_name: str = Field(min_length=1, max_length=64)
    backend_version: str = Field(min_length=1, max_length=128)
    capabilities: list[Literal["static-analysis", "debugger", "external-mcp"]] = Field(
        min_length=1, max_length=3
    )
    operations: list[PluginOperation] = Field(min_length=1, max_length=64)
    command: list[str] = Field(min_length=1, max_length=128)
    working_directory: str = Field(min_length=1, max_length=32768)
    environment: dict[str, str] = Field(default_factory=dict, max_length=32, repr=False)
    required_permissions: list[Permission] = Field(min_length=1, max_length=32)
    health_timeout_ms: int = Field(default=5000, ge=100, le=30000)
    max_memory_bytes: int = Field(default=2 * 1024**3, ge=64 * 1024**2, le=8 * 1024**3)

    @model_validator(mode="after")
    def bounded_manifest(self) -> "PluginManifest":
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("duplicate plugin capability")
        if len({op.name for op in self.operations}) != len(self.operations):
            raise ValueError("duplicate plugin operation")
        for operation in self.operations:
            if operation.capability not in self.capabilities:
                raise ValueError("plugin operation capability undeclared")
            if operation.permission_scope not in self.required_permissions:
                raise ValueError("plugin operation permission undeclared")
        if any(not arg or "\x00" in arg or len(arg.encode()) > 32768 for arg in self.command):
            raise ValueError("invalid plugin command argument")
        if sum(len(arg.encode()) for arg in self.command) > 65536:
            raise ValueError("plugin command exceeds bootstrap limit")
        if any(
            "\x00" in value or len(value.encode()) > 8192 for value in self.environment.values()
        ):
            raise ValueError("invalid plugin environment value")
        return self


class PluginRequest(StrictModel):
    type: Literal["request"] = "request"
    protocol_version: ProtocolVersion = 1
    instance_id: Identifier
    request_id: Identifier
    operation: str = Field(min_length=1, max_length=128)
    deadline_unix_ms: int = Field(ge=0, le=9999999999999)
    payload: dict[str, Any]


class PluginCancel(StrictModel):
    type: Literal["cancel"] = "cancel"
    protocol_version: ProtocolVersion = 1
    instance_id: Identifier
    request_id: Identifier


class PluginError(StrictModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=64)
    message: str = Field(max_length=4096)
    execution_state: Literal["not_started", "unknown", "completed"] = "unknown"


class PluginResult(StrictModel):
    type: Literal["result"] = "result"
    protocol_version: ProtocolVersion = 1
    instance_id: Identifier
    request_id: Identifier
    state: Literal["SUCCEEDED", "FAILED"]
    result: dict[str, Any] | None = None
    error: PluginError | None = None

    @model_validator(mode="after")
    def exclusive_result(self) -> "PluginResult":
        if self.state == "SUCCEEDED" and (self.result is None or self.error is not None):
            raise ValueError("successful plugin result requires result only")
        if self.state == "FAILED" and (self.error is None or self.result is not None):
            raise ValueError("failed plugin result requires error only")
        return self


class PluginEvent(StrictModel):
    type: Literal["event"] = "event"
    protocol_version: ProtocolVersion = 1
    instance_id: Identifier
    sequence: str = Field(pattern=r"^(0|[1-9][0-9]{0,19})$")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=64)
    data: dict[str, Any]


PluginMessage = Annotated[
    PluginRequest | PluginCancel | PluginResult | PluginEvent, Field(discriminator="type")
]
PLUGIN_MESSAGE: TypeAdapter[PluginMessage] = TypeAdapter(PluginMessage)
