"""Session-explicit text clipboard access with a mandatory compare-before-write fence."""

from pydantic import Field, model_validator

from racp_protocol.desktop import DesktopSession
from racp_protocol.models import StrictModel


class ClipboardRead(DesktopSession):
    max_bytes: int = Field(default=4096, ge=1, le=8192)


class ClipboardWrite(DesktopSession):
    text: str | None = Field(default=None, max_length=8192)
    clear: bool = False
    expected_sequence: int = Field(ge=0, le=0xFFFFFFFF)

    @model_validator(mode="after")
    def one_bounded_value(self) -> "ClipboardWrite":
        if (self.text is None) != self.clear:
            raise ValueError("Choose text or clear=true")
        if self.text is not None:
            if "\0" in self.text or len(self.text.encode("utf-8")) > 8192:
                raise ValueError("Clipboard text exceeds UTF-8 budget or contains NUL")
        return self


CLIPBOARD_MODELS: dict[str, type[StrictModel]] = {
    "clipboard.state": DesktopSession,
    "clipboard.read": ClipboardRead,
    "clipboard.write": ClipboardWrite,
}
CLIPBOARD_READS = frozenset({"clipboard.read", "clipboard.state"})
