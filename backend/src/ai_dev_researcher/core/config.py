from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKSPACE = BACKEND_ROOT / "workspace"
DEFAULT_KNOWLEDGE_BASE = BACKEND_ROOT.parent / "knowledge_base"


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
    knowledge_base_root: Path = Field(default=DEFAULT_KNOWLEDGE_BASE)

    # RAG embedding 配置：HF_HUB_CACHE 指向本地模型缓存根目录
    # （如 E:/04Programming/Models，内含 models--sentence-transformers--* 结构）。
    # 为空时使用 huggingface 默认缓存（~/.cache/huggingface/hub）。
    hf_hub_cache: str = ""
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # embedding 离线模式：为 true 时若 HF 缓存缺失则快速失败（不联网下载/5 次重试）。
    # 也可通过环境变量 HF_HUB_OFFLINE=1 临时开启。
    embedding_offline: bool = False
    # 预留：本地 GGUF embedding 模型路径（Qwen3-Embedding-0.6B-GGUF 离线候选）。
    # 当前未接入，留空即不启用。
    gguf_embedding_model_path: str = ""

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    tavily_api_key: str = ""

    # Agent D budget 护栏：0 表示不限制；可由 .env / 环境变量覆盖，也可在 run constraints 中传。
    agent_max_tool_calls: int = 60
    agent_max_elapsed_seconds: float = 600.0
    kb_prefetch_top_k: int = 5
    kb_prefetch_enabled: bool = True

    # Phase-1 vertical slice uses a fake executor until DeepAgents is wired.
    fake_agent_mode: bool = True

    max_upload_bytes: int = 50 * 1024 * 1024
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