"""Settings, read from environment variables and `.env` (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    db_path: str = "data/proofofair.db"
    keys_dir: str = "data/keys"
    anchors_dir: str = "anchors"

    athens_lat: float = 37.98
    athens_lon: float = 23.73
    athens_radius_km: float = 30

    ingest_interval_sec: int = 300
    batch_interval_sec: int = 600
    sim_devices: int = 12

    anchor_backend: str = "mock"  # mock | polygon | ots
    polygon_rpc_url: str = "https://rpc-amoy.polygon.technology"
    polygon_chain_id: int = 80002
    polygon_explorer: str = "https://amoy.polygonscan.com"
    polygon_private_key: str = ""
    anchor_contract_address: str = ""

    demo_mode: bool = True
    # Not in .env.example: tests and scripts switch these off.
    scheduler_enabled: bool = True
    sensor_community_enabled: bool = True

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).parent

    @property
    def mock_chain_path(self) -> Path:
        return self.data_dir / "mock_chain.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached settings (used by tests after changing env vars)."""
    get_settings.cache_clear()
    return get_settings()
