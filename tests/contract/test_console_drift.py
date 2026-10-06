import runpy
from pathlib import Path


def test_console_openapi_matches_application() -> None:
    document = runpy.run_path("scripts/console_contract.py")["document"]
    assert Path("docs/protocol/console-openapi-v1.json").read_text(encoding="utf-8") == document()
