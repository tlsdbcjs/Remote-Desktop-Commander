import pytest
from racp_domain.models import RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.registry import REGISTRY, validate_payload


def test_browser_rejects_local_profiles_host_urls_and_unobserved_input() -> None:
    target = {"browser_id": "browser_fixture", "page_id": "page_fixture"}
    for action, payload in (
        ("open", {"user_data_dir": "C:/Users/profile"}),
        ("open", {"args": ["--no-sandbox"]}),
        ("navigate", {**target, "url": "file:///C:/secret"}),
        ("navigate", {**target, "url": "https://user:secret@example.com"}),
        ("click", {**target, "selector": {"by": "role", "value": "button"}}),
        (
            "click",
            {
                **target,
                "observation_id": "obs_fixture",
                "navigation_revision": "1",
                "ref": "ref_fixture",
                "selector": {"by": "test_id", "value": "submit"},
            },
        ),
        ("evaluate", {**target, "expression": "() => 1"}),
        (
            "upload",
            {
                **target,
                "observation_id": "obs_fixture",
                "navigation_revision": "1",
                "selector": {"by": "test_id", "value": "file"},
                "artifact_id": "art_fixture",
                "filename": "../file.txt",
            },
        ),
        (
            "download",
            {
                **target,
                "observation_id": "obs_fixture",
                "navigation_revision": "1",
                "selector": {"by": "test_id", "value": "download"},
                "max_bytes": 1024**3 + 1,
            },
        ),
    ):
        with pytest.raises(RACPError) as rejected:
            validate_payload("browser." + action, payload)
        assert rejected.value.error.code == "INVALID_ARGUMENT"


def test_browser_evaluate_requires_mutation_authority_and_never_read_safe() -> None:
    operation = "browser.evaluate"
    assert REGISTRY[operation].side_effect and REGISTRY[operation].retry_class == "journal_only"
    assert REGISTRY[operation].permission_scope == "browser.evaluate"
    assert evaluate(operation, profile_rules("read_only")) == Decision.DENY
    assert evaluate(operation, profile_rules("standard")) == Decision.REQUIRE_APPROVAL
    assert evaluate(operation, profile_rules("trusted_personal")) == Decision.ALLOW


def test_cdp_requires_local_endpoint_and_explicit_existing_page_selection() -> None:
    from racp_cli.main import parser

    cli = parser().parse_args(
        [
            "browser",
            "attach",
            "dev_fixture",
            "--endpoint-url",
            "http://localhost:9222",
            "--context-mode",
            "existing",
            "--page-target-ids",
            '["target_1"]',
            "--key",
            "attach-cli",
        ]
    )
    assert cli.page_target_ids == ["target_1"]
    for value in (
        "http://192.168.1.1:9222",
        "ws://127.0.0.1:9222/devtools/page/id",
        "http://127.0.0.1:9222?token=secret",
        "http://user:password@127.0.0.1:9222",
        "http://example.com:9222",
        "http://0.0.0.0:9222",
    ):
        with pytest.raises(RACPError):
            validate_payload("browser.attach", {"endpoint_url": value})
    for payload in (
        {"context_mode": "existing"},
        {"page_target_ids": ["target_1"]},
        {"context_mode": "existing", "page_target_ids": ["target_1", "target_1"]},
    ):
        with pytest.raises(RACPError):
            validate_payload("browser.attach", {"endpoint_url": "http://localhost:9222", **payload})
    result = validate_payload("browser.attach", {"endpoint_url": "http://localhost:9222"})
    assert result["endpoint_url"] == "http://127.0.0.1:9222"
