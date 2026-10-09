import asyncio
import json

import pytest
from conftest import bridge
from racp_sdk.security import SecretStore
from tls_fixture import certificates

pytestmark = pytest.mark.asyncio


async def test_wrong_ca_does_not_connect(rust_live, tmp_path):
    live = rust_live
    other_ca, _, _ = certificates(tmp_path / "other-tls")
    store = SecretStore(live["state"] / "credential.bin")
    saved = store.load()
    settings = json.loads(saved["agent_settings"])
    settings["ca_file"] = str(other_ca)
    saved["agent_settings"] = json.dumps(settings)
    saved["ca_file"] = str(other_ca)
    store.save(saved)
    await bridge(live["executable"], live["state"], "start")
    await asyncio.sleep(0.5)
    status = await bridge(live["executable"], live["state"], "status")
    assert not status["connected"]
    assert (await bridge(live["executable"], live["state"], "stop"))["cleanup_status"] == "complete"
