"""Generate Console OpenAPI from the actual application and its transport models."""

import json
import tempfile
from pathlib import Path

from racp_gateway.app import create_app
from racp_protocol.console import ConsoleEvent


def document() -> str:
    with tempfile.TemporaryDirectory(prefix="racp-openapi-") as temporary:
        app = create_app(Path(temporary))
        try:
            value = app.openapi()
            value["components"]["schemas"]["ConsoleEvent"] = ConsoleEvent.model_json_schema()
            return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        finally:
            app.state.control.store.close()


if __name__ == "__main__":
    Path("docs/protocol/console-openapi-v1.json").write_text(document(), encoding="utf-8")
