"""Fixed public RE operations, independent of the installed plugin tool lists."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, RootModel, model_validator

from racp_protocol.models import Identifier, StrictModel

Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{1,16}$")]
DebuggerState = Literal["STARTING", "STOPPED", "RUNNING", "DETACHED", "EXITED", "FAILED"]


class BackendList(StrictModel):
    pass


class REOpen(StrictModel):
    backend: Identifier
    path: str = Field(min_length=1, max_length=4096)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AnalysisTarget(StrictModel):
    analysis_id: Identifier


class AnalysisInfo(AnalysisTarget):
    action: Literal["info"]


class AnalysisList(AnalysisTarget):
    action: Literal["functions", "strings"]
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)


class AnalysisAddress(AnalysisTarget):
    action: Literal["xrefs", "disassemble", "decompile"]
    address: Address
    address_kind: Literal["absolute", "module_rva"] = "absolute"
    module: str | None = Field(default=None, min_length=1, max_length=256)
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def rva_module(self) -> "AnalysisAddress":
        if (self.address_kind == "module_rva") != (self.module is not None):
            raise ValueError("module is required only for module RVA")
        return self


class REQuery(
    RootModel[
        Annotated[AnalysisInfo | AnalysisList | AnalysisAddress, Field(discriminator="action")]
    ]
):
    pass


class AnalysisRename(AnalysisTarget):
    action: Literal["rename"]
    address: Address
    name: str = Field(min_length=1, max_length=256)


class AnalysisComment(AnalysisTarget):
    action: Literal["comment"]
    address: Address
    text: str = Field(max_length=8192)


class RECommand(
    RootModel[Annotated[AnalysisRename | AnalysisComment, Field(discriminator="action")]]
):
    pass


class DebuggerLaunch(StrictModel):
    backend: Identifier
    executable: str = Field(min_length=1, max_length=4096)
    args: list[Annotated[str, Field(max_length=8192)]] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def argv_nul(self) -> "DebuggerLaunch":
        if any("\x00" in arg for arg in [self.executable, *self.args]):
            raise ValueError("NUL is forbidden in debugger argv")
        return self


class DebuggerAttach(StrictModel):
    backend: Identifier
    pid: int = Field(ge=1, le=0xFFFFFFFF)
    create_time: float = Field(gt=0, allow_inf_nan=False)
    agent_boot_id: Identifier


class DebuggerTarget(StrictModel):
    debug_id: Identifier


class DebuggerResume(DebuggerTarget):
    action: Literal["continue", "step_into", "step_over", "interrupt"]


class DebuggerBreakpoint(DebuggerTarget):
    action: Literal["set_breakpoint"]
    address: Address | None = None
    symbol: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_:.$@]*$", max_length=256
    )

    @model_validator(mode="after")
    def location(self) -> "DebuggerBreakpoint":
        if (self.address is None) == (self.symbol is None):
            raise ValueError("choose exactly one breakpoint address or symbol")
        return self


class DebuggerRemoveBreakpoint(DebuggerTarget):
    action: Literal["remove_breakpoint"]
    breakpoint_id: Identifier


class DebuggerCommand(
    RootModel[
        Annotated[
            DebuggerResume | DebuggerBreakpoint | DebuggerRemoveBreakpoint,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class DebuggerMemory(DebuggerTarget):
    address: Address
    size_bytes: int = Field(default=4096, ge=1, le=1024 * 1024)

    @model_validator(mode="after")
    def address_range(self) -> "DebuggerMemory":
        if int(self.address, 16) + self.size_bytes > 1 << 64:
            raise ValueError("debugger memory range overflows a 64-bit address")
        return self


class DebuggerWait(DebuggerTarget):
    after_sequence: str = Field(default="0", pattern=r"^(0|[1-9][0-9]{0,18})$")


RE_MODELS: dict[str, type[BaseModel]] = {
    "re.backends": BackendList,
    "re.open": REOpen,
    "re.query": REQuery,
    "re.command": RECommand,
    "re.close": AnalysisTarget,
    "re.keepalive": AnalysisTarget,
    "debugger.backends": BackendList,
    "debugger.launch": DebuggerLaunch,
    "debugger.attach": DebuggerAttach,
    "debugger.command": DebuggerCommand,
    "debugger.info": DebuggerTarget,
    "debugger.registers": DebuggerTarget,
    "debugger.read_memory": DebuggerMemory,
    "debugger.backtrace": DebuggerTarget,
    "debugger.wait": DebuggerWait,
    "debugger.close": DebuggerTarget,
    "debugger.keepalive": DebuggerTarget,
}
RE_READS = frozenset(
    {
        "re.backends",
        "re.query",
        "debugger.backends",
        "debugger.info",
        "debugger.registers",
        "debugger.read_memory",
        "debugger.backtrace",
        "debugger.wait",
    }
)
