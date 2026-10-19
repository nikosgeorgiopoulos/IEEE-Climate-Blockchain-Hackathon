"""Every test gets its own temp data dir, mock anchoring and no scheduler/network."""

from __future__ import annotations

import pytest

ENV = {
    "ANCHOR_BACKEND": "mock",
    "DEMO_MODE": "true",
    "SCHEDULER_ENABLED": "false",
    "SENSOR_COMMUNITY_ENABLED": "false",
    "SIM_DEVICES": "12",
    "BATCH_INTERVAL_SEC": "600",
    "POLYGON_PRIVATE_KEY": "",
    "ANCHOR_CONTRACT_ADDRESS": "",
}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from proofofair import anchor, config

    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.setenv("KEYS_DIR", str(tmp_path / "data" / "keys"))
    monkeypatch.setenv("ANCHORS_DIR", str(tmp_path / "anchors"))
    config.reload_settings()
    anchor.reset_backends()
    yield tmp_path
    config.reload_settings()
    anchor.reset_backends()
