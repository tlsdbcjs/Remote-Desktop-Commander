from dataclasses import replace

import pytest
from racp_agent.broker.identity import Identity
from racp_agent.broker.login_registration import LoginEndpoint
from racp_agent.broker.session_state import same_logon


def test_logoff_changed_user_and_unknown_query_are_distinct() -> None:
    assert same_logon({"identity_known": False, "logged_on": False, "user_sid": None}, "S-1-5-21-1")
    assert not same_logon(
        {"identity_known": True, "logged_on": False, "user_sid": None}, "S-1-5-21-1"
    )
    assert not same_logon(
        {"identity_known": True, "logged_on": True, "user_sid": "S-1-5-21-2"}, "S-1-5-21-1"
    )
    assert same_logon(
        {"identity_known": True, "logged_on": True, "user_sid": "S-1-5-21-1"}, "S-1-5-21-1"
    )


def test_service_endpoint_requires_exact_enabled_service_sid_and_nonadmin_identity() -> None:
    actor = Identity(1234, 1.0, "S-1-5-21-1", 0, 8192, ("S-1-5-80-1-2-3-4-5",))
    endpoint = LoginEndpoint(
        device_id="dev_test", agent_sid=actor.sid, service_sid=actor.service_sids[0]
    )
    endpoint.check_agent(actor)
    with pytest.raises(PermissionError):
        endpoint.check_agent(replace(actor, service_sids=()))
    with pytest.raises(PermissionError):
        endpoint.check_agent(replace(actor, administrator=True))
    with pytest.raises(PermissionError):
        endpoint.check_agent(replace(actor, sid="S-1-5-18"))
    with pytest.raises(PermissionError):
        LoginEndpoint(device_id="dev_test", agent_sid=actor.sid).check_agent(actor)
