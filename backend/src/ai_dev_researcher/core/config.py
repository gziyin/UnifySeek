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

    # Agent budget 收紧护栏：正数可由 .env / 环境变量覆盖并收紧 output_mode profile；
    # 0/负数不放开 profile。run constraints 可继续传入更严格的覆盖值。
    agent_max_tool_calls: int = 60
    agent_max_elapsed_seconds: float = 600.0
    # 阶段级看门狗（0 表示禁用该阶段预算）：单次 attempt 内 plan/research/report
    # 各自最多耗时；总时长仍受 agent_max_elapsed_seconds 约束，避免误降级「慢但能成功」的 run。
    agent_plan_timeout_seconds: float = 180.0
    agent_research_timeout_seconds: float = 480.0
    agent_report_timeout_seconds: float = 180.0
    # 空闲超时：事件流内连续 idle_timeout 秒无事件（模型/工具调用卡住）视为挂起，
    # 收敛为 DEGRADED。默认 300s 约等于单次模型调用最坏耗时（timeout=90 × max_retries=3）的上界。
    agent_idle_timeout_seconds: float = 300.0
    # TaskManager 硬超时 = 总预算 + grace（最后一道兜底，任务卡死时强制取消并收敛终态）。
    # 0 表示不启用硬超时（总预算为 0 时跟随禁用）。
    agent_hard_timeout_grace_seconds: float = 60.0
    # 关闭时等待 run 后台任务收敛的超时（避免进程挂死）。
    task_manager_shutdown_timeout_seconds: float = 15.0
    # stale run 回收器运行周期（秒）：回收「task 已死但 run 仍 active」的行。
    stale_reap_interval_seconds: float = 30.0
    kb_prefetch_top_k: int = 5
    kb_prefetch_enabled: bool = True
    # KB 预取超时（秒）：embedding 加载/检索慢或失败时超时跳过，不阻塞 run 启动。
    kb_prefetch_timeout_seconds: float = 15.0
    # KB 预取相关性阈值：低于该分数的 chunk 视为与问题无关，不记录证据、
    # 不发布 source.discovered/evidence.recorded（不污染来源账本）。可由 .env 覆盖。
    kb_prefetch_score_threshold: float = 0.3
    # KB 软预算：单次 run 内 KB 类工具（search/read/list/record_knowledge_base_*）
    # 调用上限。超过后后续 KB 工具短路返回空结果 + 引导提示（不触发全局
    # BUDGET_EXCEEDED）。正数可收紧 output_mode profile；0/负数不放开 profile。
    # 可由 .env（KB_MAX_TOOL_CALLS）覆盖。
    kb_max_tool_calls: int = 12

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
