"""Generate versioned schemas from the same models used by HTTP and WS adapters."""

import json
from dataclasses import asdict
from pathlib import Path

from racp_gateway.oauth import OAuthResourceConfig
from racp_protocol.artifacts import TransferComplete, TransferCreate
from racp_protocol.console import ConsoleEvent, ConsoleLogin, ConsoleSession, DoctorView
from racp_protocol.jobs import JobAcceptance, JobView
from racp_protocol.models import MESSAGE_ADAPTER, OperationInput
from racp_protocol.plugins import PLUGIN_MESSAGE, PluginManifest
from racp_protocol.registry import REGISTRY
from racp_protocol.resolutions import OperationResolutions
from racp_protocol.streams import StreamAckInput, StreamOpenInput


def documents() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1] / "docs/protocol"
    # Rust owns these local configuration contracts; preserve their published schemas.
    local_configs = {
        name: json.loads((root / name).read_text(encoding="utf-8"))
        for name in (
            "agent-service-config-v1.schema.json",
            "agent-settings-v1.schema.json",
            "plugin-installations-v1.schema.json",
        )
    }
    values = {
        **local_configs,
        "agent-protocol-v1.schema.json": MESSAGE_ADAPTER.json_schema(),
        "oauth-resource-config-v1.schema.json": OAuthResourceConfig.model_json_schema(),
        "plugin-manifest-v1.schema.json": PluginManifest.model_json_schema(),
        "plugin-stdio-v1.schema.json": PLUGIN_MESSAGE.json_schema(),
        "operation-v1.schema.json": OperationInput.model_json_schema(),
        "artifact-transfer-create-v1.schema.json": TransferCreate.model_json_schema(),
        "artifact-transfer-complete-v1.schema.json": TransferComplete.model_json_schema(),
        "terminal-stream-open-v1.schema.json": StreamOpenInput.model_json_schema(),
        "terminal-stream-ack-v1.schema.json": StreamAckInput.model_json_schema(),
        "job-acceptance-v1.schema.json": JobAcceptance.model_json_schema(),
        "job-view-v1.schema.json": JobView.model_json_schema(),
        "operation-resolutions-v1.schema.json": OperationResolutions.model_json_schema(),
        "console-session-v1.schema.json": ConsoleSession.model_json_schema(),
        "console-login-v1.schema.json": ConsoleLogin.model_json_schema(),
        "console-event-v1.schema.json": ConsoleEvent.model_json_schema(),
        "doctor-v1.schema.json": DoctorView.model_json_schema(),
        "registry-v1.json": {key: asdict(value) for key, value in REGISTRY.items()},
    }
    return {
        key: json.dumps(value, ensure_ascii=False, indent=2) + "\n" for key, value in values.items()
    }


def main() -> None:
    root = Path("docs/protocol")
    root.mkdir(parents=True, exist_ok=True)
    for name, document in documents().items():
        (root / name).write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
