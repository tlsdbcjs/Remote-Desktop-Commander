import codecs
from typing import Annotated, Literal

from pydantic import Field, model_validator

from racp_protocol.models import Identifier, ShellInput, StrictModel, WorkspaceId

PathText = Annotated[str, Field(min_length=1, max_length=4096)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FilePath(StrictModel):
    path: PathText


class FileRead(FilePath):
    encoding: str = "utf-8"
    binary: bool = False
    max_bytes: int = Field(default=65536, ge=1, le=65536)


class FileWrite(FilePath):
    content: str | None = Field(default=None, max_length=65536)
    artifact_id: Identifier | None = None
    mode: Literal["create", "replace", "append"] = "create"
    overwrite: bool = False
    encoding: str = "utf-8"
    newline: Literal["preserve", "lf", "crlf"] = "preserve"
    expected_sha256: Sha256 | None = None
    expected_revision: str | None = Field(default=None, max_length=128)
    expected_offset: str | None = Field(default=None, pattern=r"^\d{1,20}$")

    @model_validator(mode="after")
    def validate_write(self) -> "FileWrite":
        codecs.lookup(self.encoding)
        if (self.content is None) == (self.artifact_id is None):
            raise ValueError("write requires exactly one of content or artifact_id")
        if self.mode == "replace" and not self.overwrite:
            raise ValueError("replace requires overwrite=true")
        if self.mode == "append" and self.expected_offset is None:
            raise ValueError("append requires expected_offset")
        if self.content is not None and len(self.content.encode(self.encoding)) > 65536:
            raise ValueError("inline write exceeds 64 KiB")
        return self


class FileList(FilePath):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)


class FileMkdir(FilePath):
    parents: bool = False
    exist_ok: bool = False


class FileCopy(StrictModel):
    source: PathText
    destination: PathText
    destination_workspace_id: WorkspaceId | None = None
    overwrite: bool = False
    recursive: bool = False
    expected_sha256: Sha256 | None = None


class FileMove(FileCopy):
    copy_and_delete: bool = False


class FileDelete(FilePath):
    recursive: bool = False
    expected_revision: str | None = Field(default=None, max_length=128)


class FileSearch(FilePath):
    pattern: str = Field(min_length=1, max_length=256)
    case_sensitive: bool = True
    max_depth: int = Field(default=20, ge=0, le=20)
    max_results: int = Field(default=1000, ge=1, le=1000)


class ProcessList(StrictModel):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, max_length=2048)


class ProcessInspect(StrictModel):
    pid: int = Field(ge=1, le=4294967295)


class ProcessTarget(ProcessInspect):
    create_time: float = Field(gt=0)
    agent_boot_id: Identifier


class ProcessTerminate(ProcessTarget):
    force: bool = False


class ProcessWait(ProcessTarget):
    pass


SENSITIVE_PROCESS_READS = frozenset({"process.memory_regions", "process.memory_read"})


class ProcessMemoryRegions(ProcessTarget):
    start_address: str = Field(default="0x0", pattern=r"^0x[0-9a-fA-F]{1,16}$")
    limit: int = Field(default=128, ge=1, le=1024)


class ProcessMemoryRead(ProcessTarget):
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{1,16}$")
    size_bytes: int = Field(default=4096, ge=1, le=16 * 1024 * 1024)

    @model_validator(mode="after")
    def bounded_range(self) -> "ProcessMemoryRead":
        if int(self.address, 16) + self.size_bytes > 2**64:
            raise ValueError("memory address range overflows")
        return self


class ProcessSpawn(ShellInput):
    output: Literal["discard"] = "discard"

    @model_validator(mode="after")
    def require_argv(self) -> "ProcessSpawn":
        if self.mode != "argv":
            raise ValueError("process.spawn requires argv mode")
        return self
