from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKSPACE = BACKEND_ROOT / "workspace"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    workspace_root: Path = Field(default=DEFAULT_WORKSPACE)

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    tavily_api_key: str = ""

    # Phase-1 vertical slice uses a fake executor until DeepAgents is wired.
    fake_agent_mode: bool = True

    max_upload_bytes: int = 10 * 1024 * 1024
    max_uploads_per_session: int = 5
    max_normalized_chars: int = 200_000
    ws_send_queue_size: int = 256
    heartbeat_interval_seconds: float = 15.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def db_path(self) -> Path:
        return self.workspace_root / "app.db"

    @property
    def sessions_root(self) -> Path:
        return self.workspace_root / "sessions"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    settings.sessions_root.mkdir(parents=True, exist_ok=True)
    return settings
