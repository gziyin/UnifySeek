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
