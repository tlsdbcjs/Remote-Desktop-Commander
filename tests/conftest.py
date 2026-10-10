"""Shared server fixtures; legacy in-process Python Agent fixtures are retired."""

import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def reference_browser_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = Path(__file__).resolve().parents[1] / ".tools/playwright"
    if cache.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))


@pytest.fixture
def live() -> Any:
    pytest.skip("Python Agent fixture retired; native Agent acceptance is deferred")


def shell_request(
    live: dict[str, Any], argv: list[str], key: str = "test-key", **extra: Any
) -> dict[str, Any]:
    return {
        "device_id": live["device_id"],
        "operation": "shell.exec",
        "payload": {"argv": argv},
        "idempotency_key": key,
        "execution_profile_id": "trusted_personal",
        **extra,
    }
