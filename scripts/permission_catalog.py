"""Generate candidate metadata from the maintained permission specification.

Authorization mappings live in racp_protocol.permissions, not this text parser.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = (
    "system",
    "files_read",
    "files_write",
    "storage",
    "process",
    "execution",
    "terminal",
    "desktop_read",
    "desktop_input",
    "clipboard",
    "browser",
    "network",
    "memory",
    "analysis",
    "configuration",
    "sessions",
    "diagnostics",
    "artifacts",
)


def generate() -> list[dict[str, object]]:
    from racp_protocol.permissions import permission_operations

    source = (ROOT / "docs/spec/agent-permissions-and-capabilities.md").read_text(encoding="utf-8")
    section = source.split("## 5. 카테고리별", 1)[1].split("## 6.", 1)[0]
    result: list[dict[str, object]] = []
    category, title = "", ""
    for line in section.splitlines():
        match = re.match(r"### 5\.(\d+) (.+)", line)
        if match:
            category, title = CATEGORIES[int(match[1]) - 1], match[2]
        if not line.startswith("| `"):
            continue
        columns = [c.strip() for c in line.split("|")[1:-1]]
        for id in re.findall(r"`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)`", columns[0]):
            operations = list(permission_operations(id))
            status = "rpc" if operations else "cli" if id == "network.capture.ipv4" else "planned"
            result.append(
                {
                    "id": id,
                    "category": category,
                    "category_label": title,
                    "label": columns[1],
                    "description": columns[2],
                    "baseline": columns[3],
                    "implementation": status,
                    "operations": operations,
                }
            )
    if len(result) != 143 or len({p["id"] for p in result}) != 143:
        raise ValueError("permission specification must contain 143 unique candidate IDs")
    return result


if __name__ == "__main__":
    path = ROOT / "packages/protocol/src/racp_protocol/permission_catalog.json"
    path.write_text(json.dumps(generate(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
