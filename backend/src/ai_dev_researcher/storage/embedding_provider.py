from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def _model_cache_dir_names(model_name: str) -> list[str]:
    """Return candidate huggingface_hub snapshot dir names for a model repo id.

    huggingface_hub stores snapshots under ``<cache>/models--<repo--path>`` where
    slashes in the repo id are replaced by ``--``. A bare name with no org segment
    (e.g. ``all-MiniLM-L6-v2``) maps to ``models--all-MiniLM-L6-v2``, while an
    org-qualified id (e.g. ``sentence-transformers/all-MiniLM-L6-v2``) maps to
    ``models--sentence-transformers--all-MiniLM-L6-v2``.
    """
    normalized = model_name.replace("/", "--")
    return [f"models--{normalized}"]


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
        embedding_offline: bool = False,
    ):
        self._model_name = model_name
        self._hf_hub_cache = hf_hub_cache
        self._embedding_offline = embedding_offline
        self._model = None
        self._dimension = 384  # all-MiniLM-L6-v2 默认维度，懒加载后以实际为准。

    def _resolve_hf_cache_dir(self) -> str:
        """Resolve the HF hub cache root used for the model presence check."""
        import os

        if self._hf_hub_cache:
            return self._hf_hub_cache
        env = os.environ.get("HF_HUB_CACHE", "").strip()
        if env:
            return env
        return str(Path.home() / ".cache" / "huggingface" / "hub")

    def _resolve_offline(self) -> bool:
        """Offline mode = constructor flag or HF_HUB_OFFLINE env var."""
        import os

        if self._embedding_offline:
            return True
        return bool(os.environ.get("HF_HUB_OFFLINE"))

    def _model_cache_dir_exists(self, cache_dir: str | None, model_name: str) -> bool:
        """Return True when a populated HF snapshot dir exists under cache_dir."""
        if not cache_dir:
            return False
        base = Path(cache_dir)
        for dirname in _model_cache_dir_names(model_name):
            if (base / dirname).is_dir():
                return True
        return False

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
            cache_dir = self._resolve_hf_cache_dir()
            has_local_cache = self._model_cache_dir_exists(cache_dir, self._model_name)
            offline = self._resolve_offline()
            if offline and not has_local_cache:
                expected = (
                    cache_dir.rstrip("\\/") + "/models--" + self._model_name.replace("/", "--")
                )
                raise RuntimeError(
                    f"embedding model '{self._model_name}' is missing from HF cache "
                    f"'{cache_dir}' (expected e.g. {expected}) and offline mode is enabled "
                    f"(HF_HUB_OFFLINE=1 or embedding_offline=true). Download the model once "
                    "or point HF_HUB_CACHE at a populated cache."
                )
            if offline or has_local_cache:
                import os

                os.environ["HF_HUB_OFFLINE"] = "1"
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
        raise NotImplementedError(
            "GGUFEmbeddingProvider is a reserved placeholder for local GGUF embedding "
            "models (e.g. Qwen/Qwen3-Embedding-0.6B-GGUF). It is not wired into startup; "
            "when unset, RAG embedding degrades to no-RAG instead of blocking boot."
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        return []

    @property
    def dimension(self) -> int:
        return 0