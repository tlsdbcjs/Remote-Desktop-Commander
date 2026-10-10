"""Persistent settings and management metadata with optimistic revision checks."""

import json

from racp_domain.models import RACPError
from racp_protocol.management import SettingsPatch, SettingsView
from racp_protocol.models import timestamp

from racp_gateway.store import GatewayStore


class ManagementStore:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def get_revision(self) -> int:
        row = self.store.db.execute("SELECT revision FROM gateway_settings WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("Gateway management schema is not initialized")
        return int(row[0])

    def settings(self) -> SettingsView:
        row = self.store.db.execute(
            "SELECT revision,settings_json FROM gateway_settings WHERE id=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Gateway management schema is not initialized")
        return SettingsView(revision=int(row[0]), values=json.loads(row[1]))

    def compare_and_set(self, expected_revision: int, patch: SettingsPatch) -> SettingsView:
        if patch.expected_revision != expected_revision:
            raise RACPError("CONFLICT", "settings revision differs from the request")
        with self.store.transaction():
            current = self.settings()
            if current.revision != expected_revision:
                raise RACPError("CONFLICT", "settings revision changed")
            values = {**current.values, **patch.changes}
            cursor = self.store.db.execute(
                "UPDATE gateway_settings SET revision=revision+1,settings_json=?,updated_at=? "
                "WHERE id=1 AND revision=?",
                (json.dumps(values, sort_keys=True), timestamp(), expected_revision),
            )
            if cursor.rowcount != 1:
                raise RACPError("CONFLICT", "settings revision changed")
        return self.settings()
