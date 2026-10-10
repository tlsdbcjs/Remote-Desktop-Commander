from dataclasses import dataclass
from enum import StrEnum

from racp_protocol.clipboard import CLIPBOARD_READS
from racp_protocol.desktop import DESKTOP_MODELS, DESKTOP_READS
from racp_protocol.native_operations import NATIVE_MODELS
from racp_protocol.os_observation import OS_OBSERVATION_READS
from racp_protocol.provider_models import SENSITIVE_PROCESS_READS
from racp_protocol.proxy import PROXY_MODELS
from racp_protocol.reversing import RE_MODELS, RE_READS


class Decision(StrEnum):
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    ALLOW = "allow"


@dataclass(frozen=True)
class Rule:
    operations: frozenset[str]
    decision: Decision


def evaluate(operation: str, rules: tuple[Rule, ...]) -> Decision:
    decisions = {rule.decision for rule in rules if operation in rule.operations}
    if Decision.DENY in decisions or not decisions:
        return Decision.DENY
    if Decision.REQUIRE_APPROVAL in decisions:
        return Decision.REQUIRE_APPROVAL
    return Decision.ALLOW


def profile_rules(profile: str) -> tuple[Rule, ...]:
    reads = frozenset(
        {
            *DESKTOP_READS,
            *CLIPBOARD_READS,
            *OS_OBSERVATION_READS,
            *RE_READS,
            "filesystem.read",
            "filesystem.stat",
            "filesystem.list",
            "filesystem.search",
            "filesystem.search_content",
            "filesystem.hash",
            "process.list",
            "process.inspect",
            "process.wait",
            "process.tree",
            "terminal.read",
            "browser.pages",
            "browser.frames",
            "browser.snapshot",
            "browser.screenshot",
        }
    )
    mutations = frozenset(
        {
            *NATIVE_MODELS,
            *PROXY_MODELS,
            *[name for name in DESKTOP_MODELS if name not in DESKTOP_READS],
            *[name for name in RE_MODELS if name not in RE_READS],
            "shell.exec",
            "network.capture",
            "process.dump",
            "clipboard.write",
            "filesystem.write",
            "filesystem.patch",
            "filesystem.mkdir",
            "filesystem.copy",
            "filesystem.move",
            "filesystem.delete",
            "process.spawn",
            "process.terminate",
            "terminal.open",
            "terminal.write",
            "terminal.resize",
            "terminal.close",
            "terminal.keepalive",
            "browser.open",
            "browser.attach",
            "browser.cdp_targets",
            "browser.new_page",
            "browser.navigate",
            "browser.click",
            "browser.type",
            "browser.key",
            "browser.evaluate",
            "browser.download",
            "browser.upload",
            "browser.close_page",
            "browser.close",
            "browser.keepalive",
        }
    )
    if profile == "trusted_personal":
        return (Rule(reads | mutations | SENSITIVE_PROCESS_READS, Decision.ALLOW),)
    if profile == "standard":
        return (
            Rule(reads, Decision.ALLOW),
            Rule(mutations | SENSITIVE_PROCESS_READS, Decision.REQUIRE_APPROVAL),
        )
    return (Rule(reads, Decision.ALLOW),)
