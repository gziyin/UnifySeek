from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class EmbeddingProvider(ABC):
    """Embedding provider abstraction.

    通过抽象接口解耦向量存储与具体 embedding 实现：
    - SentenceTransformersProvider：本地 transformer 模型（默认 all-MiniLM-L6-v2，约 80MB）。
    - GGUFEmbeddingProvider：预留基于 llama-cpp 的本地 GGUF 模型（后续可接入
      Qwen/Qwen3-Embedding-0.6B-GGUF 等量化模型）。
    - 自定义 provider：用户可注入任意远程/本地 embedding 服务。
    """

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @property
    @abstractmethod
    def dimension(self) -> int:
        raise NotImplementedError


class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        hf_hub_cache: str = "",
    ):
        self._model_name = model_name
        self._hf_hub_cache = hf_hub_cache
        self._model = None
        self._dimension = 384  # all-MiniLM-L6-v2 默认维度，懒加载后以实际为准。

    def _ensure_model(self):
        if self._model is not None:
            return
        try:
            # Windows DLL 加载顺序防护：torch 必须先于 transformers 加载。
            from ai_dev_researcher.storage.torch_guard import ensure_torch_loaded

            ensure_torch_loaded()
            if self._hf_hub_cache:
                import os

                os.environ["HF_HUB_CACHE"] = self._hf_hub_cache
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            try:
                self._dimension = int(self._model.get_embedding_dimension())
            except AttributeError:
                self._dimension = int(self._model.get_sentence_embedding_dimension())
            logger.info("loaded embedding model %s (dim=%s)", self._model_name, self._dimension)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"failed to load embedding model {self._model_name}: {exc}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        vectors = self._model.encode(texts, normalize_embeddings=True)  # type: ignore[union-attr]
        return [list(map(float, row)) for row in vectors]

    @property
    def dimension(self) -> int:
        self._ensure_model()
        return self._dimension


class GGUFEmbeddingProvider(EmbeddingProvider):
    """预留：基于 llama-cpp 加载本地 GGUF embedding 模型。

    使用场景：无网络/低资源环境，复用用户已下载的
    Qwen/Qwen3-Embedding-0.6B-GGUF 等量化模型，避免每次下载 80MB 模型。
    当前为占位实现，调用 embed 时抛出 NotImplementedError。
    """

    def __init__(self, model_path: str | None = None):
        self._model_path = model_path
        self._model = None

    def _ensure_model(self):
        raise NotImplementedError("GGUF embedding provider is reserved for future use")

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        return []

    @property
    def dimension(self) -> int:
        return 0
