import pytest
from racp_domain.models import RACPError
from racp_policy.engine import Decision, evaluate, profile_rules
from racp_protocol.permissions import required_permissions
from racp_protocol.registry import validate_payload


def valid():
    return {
        "pid": 1234,
        "create_time": 123.5,
        "agent_boot_id": "boot_own",
        "direction": "agent_connector",
        "local_port": 50555,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"local_port": None},
        {"local_port": True},
        {"local_port": 0},
        {"direction": "agent_listener"},
        {"host": "0.0.0.0"},
        {"url": "https://arbitrary.invalid"},
        {"max_bytes": 67108865},
        {"lease_seconds": True},
        {"lease_seconds": 3601},
        {"pid": True},
    ],
)
def test_proxy_rejects_unbound_untyped_or_unbounded_inputs(change):
    with pytest.raises(RACPError):
        validate_payload("proxy.prepare", {**valid(), **change})


def test_proxy_has_independent_sensitive_and_broad_execution_gates():
    payload = validate_payload("proxy.prepare", valid())
    assert set(required_permissions("proxy.prepare", payload)) == {
        "network.proxy.intercept",
        "network.http.replay",
        "network.tunnel.open",
        "exec.argv",
        "artifacts.export",
    }
    assert evaluate("proxy.prepare", profile_rules("read_only")) == Decision.DENY
    assert evaluate("proxy.prepare", profile_rules("standard")) == Decision.REQUIRE_APPROVAL


def test_agent_selected_listener_port_and_scope_role():
    payload = validate_payload(
        "proxy.prepare", {**valid(), "direction": "agent_listener", "local_port": None}
    )
    assert payload["local_port"] is None
