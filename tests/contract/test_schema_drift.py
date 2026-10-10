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


def test_published_schemas_match_runtime_models() -> None:
    root = Path(__file__).resolve().parents[2] / "docs/protocol"
    assert (
        json.loads((root / "agent-protocol-v1.schema.json").read_text(encoding="utf-8"))
        == MESSAGE_ADAPTER.json_schema()
    )
    assert (
        json.loads((root / "operation-v1.schema.json").read_text(encoding="utf-8"))
        == OperationInput.model_json_schema()
    )
    assert (
        json.loads((root / "plugin-stdio-v1.schema.json").read_text(encoding="utf-8"))
        == PLUGIN_MESSAGE.json_schema()
    )
    assert json.loads((root / "registry-v1.json").read_text(encoding="utf-8")) == json.loads(
        json.dumps({key: asdict(value) for key, value in REGISTRY.items()})
    )
    for name, model in (
        ("plugin-manifest-v1.schema.json", PluginManifest),
        ("oauth-resource-config-v1.schema.json", OAuthResourceConfig),
        ("artifact-transfer-create-v1.schema.json", TransferCreate),
        ("artifact-transfer-complete-v1.schema.json", TransferComplete),
        ("terminal-stream-open-v1.schema.json", StreamOpenInput),
        ("terminal-stream-ack-v1.schema.json", StreamAckInput),
        ("job-acceptance-v1.schema.json", JobAcceptance),
        ("job-view-v1.schema.json", JobView),
        ("operation-resolutions-v1.schema.json", OperationResolutions),
        ("console-session-v1.schema.json", ConsoleSession),
        ("console-login-v1.schema.json", ConsoleLogin),
        ("console-event-v1.schema.json", ConsoleEvent),
        ("doctor-v1.schema.json", DoctorView),
    ):
        assert json.loads((root / name).read_text(encoding="utf-8")) == model.model_json_schema()
