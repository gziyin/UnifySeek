from __future__ import annotations

from pathlib import Path

import pytest

from ai_dev_researcher.storage.vector_store import (
    Chunk,
    split_text_into_chunks,
    VectorStore,
)


class FakeEmbeddingProvider:
    """确定性 embedding：按文本首字符映射到单位向量，便于断言检索相关性。"""

    def __init__(self, dimension: int = 8):
        self._dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self._dimension
            for ch in text:
                vec[ord(ch) % self._dimension] += 1.0
            norm = sum(v * v for v in vec) ** 0.5
            if norm:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors

    @property
    def dimension(self) -> int:
        return self._dimension


def test_split_text_by_paragraph():
    text = "para one\n\npara two\n\npara three"
    chunks = split_text_into_chunks(text)
    assert len(chunks) == 3
    assert chunks[0][0] == "para one"
    assert chunks[1][1] == 10  # start offset 与段落分割一致


def test_split_long_paragraph_sliding_window():
    text = "x" * 4000  # 超过默认 512 token * 4 字符窗口
    chunks = split_text_into_chunks(text, max_tokens=512)
    assert len(chunks) > 1
    assert all(len(c[0]) <= 512 * 4 for c in chunks)
    # 首块起点 0，末块终点等于原文长度（近似窗口内）。
    assert chunks[0][1] == 0
    assert chunks[-1][2] == len(text)


def test_vector_store_index_and_retrieve(tmp_path: Path):
    store = VectorStore(
        persist_dir=tmp_path / "chroma",
        embedding_provider=FakeEmbeddingProvider(),
    )
    if not store.available:
        pytest.skip("chromadb not installed")

    text = "DeepAgents orchestrates specialized subagents for research.\n\n" \
           "Tavily searches the web for evidence.\n\n" \
           "The report aggregates claims with citations."
    count = store.index_document(artifact_id="art-1", text=text)
    assert count == 3

    results = store.retrieve(query="orchestration", artifact_ids=["art-1"], top_k=2)
    assert len(results) == 2
    assert all(isinstance(c, Chunk) for c in results)
    assert all(c.artifact_id == "art-1" for c in results)
    # 相关性：包含 orchestrates 的 chunk 应在结果中（top1 至少包含其一）。
    assert any("orchestrates" in c.text or "aggregates" in c.text for c in results)


def test_retrieve_empty_artifact_ids(tmp_path: Path):
    store = VectorStore(
        persist_dir=tmp_path / "chroma",
        embedding_provider=FakeEmbeddingProvider(),
    )
    if not store.available:
        pytest.skip("chromadb not installed")
    assert store.retrieve(query="q", artifact_ids=[]) == []


def test_retrieve_scoped_to_artifact(tmp_path: Path):
    store = VectorStore(
        persist_dir=tmp_path / "chroma",
        embedding_provider=FakeEmbeddingProvider(),
    )
    if not store.available:
        pytest.skip("chromadb not installed")

    store.index_document(artifact_id="a", text="alpha document content")
    store.index_document(artifact_id="b", text="beta document content")
    results = store.retrieve(query="beta", artifact_ids=["b"], top_k=3)
    assert results
    assert all(c.artifact_id == "b" for c in results)


def test_reindex_same_artifact_replaces(tmp_path: Path):
    store = VectorStore(
        persist_dir=tmp_path / "chroma",
        embedding_provider=FakeEmbeddingProvider(),
    )
    if not store.available:
        pytest.skip("chromadb not installed")

    store.index_document(artifact_id="a", text="first version content")
    store.index_document(artifact_id="a", text="second version content replaced")
    results = store.retrieve(query="second", artifact_ids=["a"], top_k=5)
    assert results
    assert all(c.artifact_id == "a" for c in results)


def test_vector_store_adds_in_batches(tmp_path):
    from ai_dev_researcher.storage import vector_store as vs

    class FakeCollection:
        def __init__(self):
            self.add_calls: list[list[str]] = []
            self.deleted_where: list[dict] = []
            self.deleted_ids: list[str] = []

        def add(self, *, ids, documents, embeddings, metadatas):
            assert len(ids) <= vs._ADD_BATCH_SIZE
            self.add_calls.append(ids)

        def delete(self, *, where=None, ids=None):
            if where is not None:
                self.deleted_where.append(where)
            if ids:
                self.deleted_ids.extend(ids)

    store = VectorStore(
        persist_dir=tmp_path / "chroma",
        embedding_provider=FakeEmbeddingProvider(),
    )
    fake = FakeCollection()
    store._collection = fake  # type: ignore[assignment]
    store._client = object()

    # 600 paragraphs x ~2 chunks each -> > 1000 chunks, crossing a batch boundary.
    text = "\n\n".join("para " + "y" * 3000 for _ in range(600))
    count = store.index_document(artifact_id="big", text=text)
    assert count > vs._ADD_BATCH_SIZE
    assert len(fake.add_calls) >= 2
    assert all(len(call) <= vs._ADD_BATCH_SIZE for call in fake.add_calls)
    assert fake.deleted_where == [{"artifact_id": "big"}]


# --- Issue #8: offline embedding cache control --------------------------------

def test_model_cache_dir_names_bare_and_org():
    from ai_dev_researcher.storage.embedding_provider import _model_cache_dir_names

    assert _model_cache_dir_names("all-MiniLM-L6-v2") == ["models--all-MiniLM-L6-v2"]
    assert _model_cache_dir_names("sentence-transformers/all-MiniLM-L6-v2") == [
        "models--sentence-transformers--all-MiniLM-L6-v2"
    ]


def test_sentence_transformers_provider_cache_presence(tmp_path: Path):
    from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider

    cache = tmp_path / "hf-cache"
    (cache / "models--all-MiniLM-L6-v2").mkdir(parents=True)
    provider = SentenceTransformersProvider(model_name="all-MiniLM-L6-v2", hf_hub_cache=str(cache))
    assert provider._model_cache_dir_exists(str(cache), "all-MiniLM-L6-v2") is True
    assert provider._model_cache_dir_exists(str(cache), "other-model") is False
    assert provider._model_cache_dir_exists("", "all-MiniLM-L6-v2") is False


def test_offline_missing_cache_fails_fast(tmp_path: Path, monkeypatch):
    from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider

    cache = tmp_path / "hf-cache"
    cache.mkdir()
    provider = SentenceTransformersProvider(
        model_name="all-MiniLM-L6-v2",
        hf_hub_cache=str(cache),
        embedding_offline=True,
    )
    with pytest.raises(RuntimeError, match="all-MiniLM-L6-v2"):
        provider._ensure_model()
    # 失败是"快速失败"路径：不应把会话标记为可在线下载。
    assert provider._model is None


def test_env_hf_offline_missing_cache_fails_fast(tmp_path: Path, monkeypatch):
    from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    cache = tmp_path / "hf-cache"
    cache.mkdir()
    provider = SentenceTransformersProvider(model_name="all-MiniLM-L6-v2", hf_hub_cache=str(cache))
    with pytest.raises(RuntimeError, match="all-MiniLM-L6-v2"):
        provider._ensure_model()


def test_offline_with_local_cache_loads_and_sets_hf_offline(tmp_path: Path, monkeypatch):
    import os
    import sys
    import types

    from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider

    cache = tmp_path / "hf-cache"
    (cache / "models--sentence-transformers--all-MiniLM-L6-v2").mkdir(parents=True)

    fake_st = types.ModuleType("sentence_transformers")

    class FakeModel:
        def __init__(self, name: str):
            self._name = name

        def get_sentence_embedding_dimension(self) -> int:
            return 384

    fake_st.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    provider = SentenceTransformersProvider(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        hf_hub_cache=str(cache),
        embedding_offline=True,
    )
    provider._ensure_model()
    assert provider._model is not None
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert provider.dimension == 384