import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from racp_protocol.models import Identifier, StrictModel


class BrowserOpen(StrictModel):
    headless: bool = True
    width: int = Field(default=1280, ge=320, le=1920)
    height: int = Field(default=720, ge=240, le=1080)


def cdp_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname
    if host == "localhost":
        host = "127.0.0.1"
    try:
        local = ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        local = False
    if (
        not local
        or parsed.scheme not in {"http", "https", "ws", "wss"}
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme in {"http", "https"}
        and parsed.path not in {"", "/"}
        or parsed.scheme in {"ws", "wss"}
        and not parsed.path.startswith("/devtools/browser/")
    ):
        raise ValueError(
            "CDP requires a credential-free loopback browser endpoint with an explicit port"
        )
    assert host is not None
    literal = "[" + host + "]" if ":" in host else host
    return f"{parsed.scheme}://{literal}:{parsed.port}{parsed.path}"


class BrowserCDPTargets(StrictModel):
    endpoint_url: str = Field(max_length=2048)

    @model_validator(mode="after")
    def local_endpoint(self) -> "BrowserCDPTargets":
        self.endpoint_url = cdp_endpoint(self.endpoint_url)
        return self


class BrowserAttach(BrowserCDPTargets):
    context_mode: Literal["isolated", "existing"] = "isolated"
    page_target_ids: list[Identifier] = Field(default_factory=list, max_length=8)
    allow_page_termination: bool = False
    width: int = Field(default=1280, ge=320, le=1920)
    height: int = Field(default=720, ge=240, le=1080)

    @model_validator(mode="after")
    def explicit_pages(self) -> "BrowserAttach":
        if (
            self.context_mode == "existing"
            and not self.page_target_ids
            or self.context_mode == "isolated"
            and (self.page_target_ids or self.allow_page_termination)
            or len(set(self.page_target_ids)) != len(self.page_target_ids)
        ):
            raise ValueError("existing context requires explicit distinct page target IDs")
        return self


class BrowserTarget(StrictModel):
    browser_id: Identifier
    agent_boot_id: Identifier | None = None


class BrowserPage(BrowserTarget):
    page_id: Identifier


class BrowserFrame(BrowserPage):
    frame_id: Identifier | None = None


class BrowserFrames(BrowserPage):
    limit: int = Field(default=16, ge=1, le=64)
    cursor: Identifier | None = None


class BrowserNavigate(BrowserFrame):
    url: str = Field(max_length=8192)

    @model_validator(mode="after")
    def safe_url(self) -> "BrowserNavigate":
        url = urlsplit(self.url)
        _ = url.port  # Validate the port before dispatch.
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("HTTP(S) URL without credentials required")
        if any(ord(char) < 32 for char in self.url):
            raise ValueError("URL contains control characters")
        return self


class BrowserLocator(StrictModel):
    by: Literal["role", "test_id"]
    value: str = Field(min_length=1, max_length=256)
    name: str | None = Field(default=None, max_length=256)


class BrowserObserved(BrowserFrame):
    observation_id: Identifier
    navigation_revision: str = Field(pattern=r"^\d{1,20}$")


class BrowserElement(BrowserObserved):
    ref: Identifier | None = None
    selector: BrowserLocator | None = None

    @model_validator(mode="after")
    def one_target(self) -> "BrowserElement":
        if (self.ref is None) == (self.selector is None):
            raise ValueError("choose element ref or structured selector")
        return self


class BrowserType(BrowserElement):
    text: str = Field(max_length=16384)


class BrowserKey(BrowserObserved):
    key: str = Field(min_length=1, max_length=128)


class BrowserEvaluate(BrowserObserved):
    expression: str = Field(min_length=1, max_length=16384)


class BrowserDownload(BrowserElement):
    max_bytes: int = Field(default=1024**3, ge=1, le=1024**3)


class BrowserUpload(BrowserElement):
    artifact_id: Identifier
    filename: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def basename(self) -> "BrowserUpload":
        if (
            self.filename in {".", ".."}
            or self.filename[-1] in {" ", "."}
            or any(c in '/\\:<>"|?*' or ord(c) < 32 for c in self.filename)
            or self.filename.split(".")[0].rstrip(" .").upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                "CLOCK$",
                "CONIN$",
                "CONOUT$",
                *[f"COM{i}" for i in "¹²³"],
                *[f"LPT{i}" for i in "¹²³"],
                *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)],
            }
        ):
            raise ValueError("safe file basename required")
        return self


BROWSER_MODELS: dict[str, type[StrictModel]] = {
    "browser.open": BrowserOpen,
    "browser.attach": BrowserAttach,
    "browser.cdp_targets": BrowserCDPTargets,
    "browser.pages": BrowserTarget,
    "browser.new_page": BrowserTarget,
    "browser.close": BrowserTarget,
    "browser.keepalive": BrowserTarget,
    "browser.close_page": BrowserPage,
    "browser.navigate": BrowserNavigate,
    "browser.snapshot": BrowserFrame,
    "browser.frames": BrowserFrames,
    "browser.screenshot": BrowserPage,
    "browser.click": BrowserElement,
    "browser.type": BrowserType,
    "browser.key": BrowserKey,
    "browser.evaluate": BrowserEvaluate,
    "browser.download": BrowserDownload,
    "browser.upload": BrowserUpload,
}
