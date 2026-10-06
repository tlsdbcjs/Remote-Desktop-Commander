from pathlib import Path

from pydantic import Field, model_validator
from racp_protocol.models import StrictModel

from racp_agent.plugins.manifest import decode_json
from racp_agent.providers.paths import is_link


class PluginInstallation(StrictModel):
    manifest: Path
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    permissions: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def local_configuration(self) -> "PluginInstallation":
        if not self.manifest.is_absolute() or str(self.manifest).startswith("\\\\"):
            raise ValueError("plugin manifest must be an absolute local file")
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("duplicate plugin permission grant")
        return self


class PluginConfig(StrictModel):
    version: int = Field(default=1, ge=1, le=1)
    plugins: list[PluginInstallation] = Field(default_factory=list, max_length=4)


def load_plugin_config(path: Path | None) -> PluginConfig:
    if path is None:
        return PluginConfig()
    if not path.is_absolute() or any(is_link(p.lstat()) for p in [path, *path.parents]):
        raise PermissionError("plugin configuration must be an explicit local file without links")
    with path.open("rb") as stream:
        raw = stream.read(65537)
    decode_json(raw, 65536)
    return PluginConfig.model_validate_json(raw)
