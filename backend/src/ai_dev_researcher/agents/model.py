from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.core.errors import ConfigurationError


@dataclass(frozen=True)
class ModelBinding:
    spec: str
    instance: BaseChatModel


def create_model_binding(settings: Settings) -> ModelBinding:
    if not settings.deepseek_api_key:
        raise ConfigurationError("DEEPSEEK_API_KEY is required for agent mode")
    spec = f"deepseek:{settings.deepseek_model}"
    return ModelBinding(
        spec=spec,
        instance=ChatDeepSeek(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            temperature=0,
            max_retries=2,
            timeout=90,
        ),
    )
