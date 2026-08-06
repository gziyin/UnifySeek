"""Unit tests for WP-A KnowledgeIndex + search_knowledge_base (storage/knowledge_index.py).

Chroma-backed tests are skipped when chromadb is not installed so the suite
stays offline-runnable; the fake embedding provider keeps them key-free.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import re
from pathlib import Path
from uuid import uuid4

import pytest

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.storage.knowledge_index import KnowledgeIndex
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.knowledge_base import (
    search_knowledge_base_impl,
    set_knowledge_index,
)


def _chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
        return True
    except ImportError:
        return False


needs_chroma = pytest.mark.skipif(not _chroma_available(), reason="chromadb not installed")


class FakeEmbeddingProvider:
    """Deterministic offline embedding provider (token-hash bag of words)."""

    def __init__(self, dim: int = 64):
        self._dim = dim
        self.embedded_texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [_vector(t, self._dim) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim


def _vector(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower()):
        digest = hashlib.md5(token.encode("utf-8")).hexdigest()
        vec[int(digest, 16) % dim] += 1.0
    if not any(vec):
        vec[0] = 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    root.mkdir()
    (root / "math_utils.py").write_text(
        '"""Math utilities for vector math."""\n\n'
        "def add(a, b):\n"
        '    """Add two numbers."""\n'
        "    return a + b\n\n\n"
        "def subtract(a, b):\n"
        "    return a - b\n",
        encoding="utf-8",
    )
    (root / "notes.md").write_text(
        "# Notes\n\nKnowledge base indexing notes about embeddings and chunks.\n",
        encoding="utf-8",
    )
    # These directories must be skipped by the scanner.
    (root / ".venv").mkdir()
    (root / ".venv" / "skip_me.py").write_text("x = 1\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "cache.py").write_text("y = 2\n", encoding="utf-8")
    return root


@pytest.fixture
def provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


def _make_index(kb_root: Path, provider: FakeEmbeddingProvider, tmp_path: Path) -> KnowledgeIndex:
    return KnowledgeIndex(
        kb_root=kb_root,
        persist_dir=tmp_path / "vector_store",
        embedding_provider=provider,
    )


# ---------------------------------------------------------------------------
# KnowledgeIndex
# ---------------------------------------------------------------------------


@needs_chroma
def test_rebuild_indexes_files_and_retrieves(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    assert not index.is_ready
    count = index.rebuild()
    assert count > 0
    assert index.is_ready
    assert index.last_chunk_count == count

    results = index.retrieve("add", top_k=10)
    assert results
    add_chunks = [r for r in results if r.symbol == "add"]
    assert add_chunks
    first = add_chunks[0]
    assert first.file_path == "math_utils.py"
    assert first.kind == "function"
    assert first.line_start == 3
    assert first.score > 0.0
    assert "def add" in first.text

    # Skipped directories are not indexed.
    paths = {c.file_path for c in index.retrieve("skip_me", top_k=50)}
    assert "skip_me.py" not in paths
    assert ".venv/skip_me.py" not in paths
    assert "__pycache__/cache.py" not in paths


@needs_chroma
def test_retrieve_path_filter(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()

    notes = index.retrieve("notes", path="notes.md", top_k=10)
    assert notes
    assert all(r.file_path == "notes.md" for r in notes)

    py = index.retrieve("notes", path="math_utils.py", top_k=10)
    assert all(r.file_path == "math_utils.py" for r in py)


@needs_chroma
def test_top_k_and_score_threshold(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()

    assert len(index.retrieve("add", top_k=1)) == 1
    all_results = index.retrieve("add", top_k=10)
    assert len(all_results) >= 1

    strict = index.retrieve("add", top_k=10, score_threshold=0.5)
    assert strict
    assert all(r.score >= 0.5 for r in strict)

    # A query with no shared tokens is filtered out by a small threshold.
    empty = index.retrieve("zzzznotthere", top_k=10, score_threshold=0.01)
    assert not empty or all(r.score < 0.01 for r in empty)


@needs_chroma
def test_delete_sync_removes_stale(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()
    assert any(r.file_path == "math_utils.py" for r in index.retrieve("add", top_k=10))

    (kb_root / "math_utils.py").unlink()
    index.rebuild()

    after = index.retrieve("add", top_k=10)
    assert not any(r.file_path == "math_utils.py" for r in after)
    # Unchanged files stay indexed.
    assert any(r.file_path == "notes.md" for r in index.retrieve("notes", top_k=10))


@needs_chroma
def test_double_encoding_summary_is_embedded(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()

    # Embeddings were computed from heuristic summaries, not raw chunk text.
    assert provider.embedded_texts
    assert any("Add two numbers" in s for s in provider.embedded_texts)
    assert all(
        any(
            token in s
            for token in ("function", "class", "text", "markdown", "module", "export", "method")
        )
        for s in provider.embedded_texts
    )
    # Retrieved text is the original source, not the summary.
    results = index.retrieve("add", top_k=3)
    assert any("def add" in r.text for r in results)
    # Summaries are much shorter than the raw chunk text they stand for.
    raw_texts = [r.text for r in results]
    assert any(len(s) < 120 for s in provider.embedded_texts)
    assert all(len(s) < 300 for s in provider.embedded_texts)
    assert raw_texts


@needs_chroma
def test_incremental_rebuild_skips_unchanged_files(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()
    first_embedded = list(provider.embedded_texts)
    provider.embedded_texts.clear()

    index.rebuild()  # nothing changed -> no re-embedding
    assert provider.embedded_texts == []
    assert index.is_ready


@needs_chroma
def test_rebuild_excludes_tests_evals_and_data_json(kb_root, provider, tmp_path):
    (kb_root / "tests").mkdir()
    (kb_root / "tests" / "test_util.py").write_text(
        '"""Test util."""\n\ndef test_x():\n    assert True\n', encoding="utf-8"
    )
    (kb_root / "evals").mkdir()
    (kb_root / "evals" / "eval_util.py").write_text(
        '"""Eval util."""\n\neval_result = "ok"\n', encoding="utf-8"
    )
    (kb_root / "data").mkdir()
    (kb_root / "data" / "db.json").write_text(
        '{"table": "seed-data"}\n', encoding="utf-8"
    )

    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()

    assert not any(
        c.file_path == "tests/test_util.py" for c in index.retrieve("test_x", top_k=50)
    )
    assert not any(
        c.file_path == "evals/eval_util.py" for c in index.retrieve("eval_result", top_k=50)
    )
    assert not any(
        c.file_path == "data/db.json" for c in index.retrieve("seed-data", top_k=50)
    )


@needs_chroma
def test_file_chunk_cap_truncates(provider, tmp_path, monkeypatch):
    from ai_dev_researcher.storage import knowledge_index as ki
    from ai_dev_researcher.storage.code_chunker import ChunkInfo

    fake_chunks = [
        ChunkInfo(f"chunk {i}", f"sym{i}", "text", "", i, i + 1) for i in range(6000)
    ]
    monkeypatch.setattr(ki, "chunk_file", lambda source, name, max_tokens: fake_chunks)
    monkeypatch.setattr(ki, "generate_summary", lambda *a, **k: "summary")

    root = tmp_path / "kb_cap"
    root.mkdir()
    f = root / "big.py"
    f.write_text("x = 1\n", encoding="utf-8")

    index = KnowledgeIndex(
        kb_root=root, persist_dir=tmp_path / "vs", embedding_provider=provider
    )
    items = index._index_file(f)
    assert len(items) == ki._MAX_FILE_CHUNKS
    assert len(items) <= 5000


@needs_chroma
def test_rebuild_adds_in_batches(provider, tmp_path, monkeypatch):
    from ai_dev_researcher.storage import knowledge_index as ki
    from ai_dev_researcher.storage.code_chunker import ChunkInfo

    class FakeCollection:
        def __init__(self):
            self.add_calls: list[list[str]] = []
            self.deleted_where: list[dict] = []

        def get(self, **kwargs):
            return {"metadatas": []}

        def delete(self, *, where=None, ids=None):
            del ids
            if where is not None:
                self.deleted_where.append(where)

        def add(self, *, ids, documents, embeddings, metadatas):
            assert len(ids) <= ki._ADD_BATCH_SIZE
            self.add_calls.append(ids)

    fake_chunks = [
        ChunkInfo(f"chunk {i}", f"sym{i}", "text", "", i, i + 1) for i in range(2500)
    ]
    monkeypatch.setattr(ki, "chunk_file", lambda source, name, max_tokens: fake_chunks)
    monkeypatch.setattr(ki, "generate_summary", lambda *a, **k: "summary")

    root = tmp_path / "kb_batch"
    root.mkdir()
    (root / "big.py").write_text("x = 1\n", encoding="utf-8")

    index = KnowledgeIndex(
        kb_root=root, persist_dir=tmp_path / "vs", embedding_provider=provider
    )
    fake = FakeCollection()
    index._collection = fake  # type: ignore[assignment]
    index._client = object()

    count = index.rebuild()
    assert count == 2500
    assert len(fake.add_calls) == 3
    assert all(len(call) <= 1000 for call in fake.add_calls)
    assert fake.deleted_where == [{"file_path": "big.py"}]


# ---------------------------------------------------------------------------
# search_knowledge_base_impl
# ---------------------------------------------------------------------------


async def test_not_ready_returns_indexing_note():
    set_knowledge_index(None)
    result = await search_knowledge_base_impl("anything")
    assert result == {"results": [], "note": "indexing"}


@needs_chroma
async def test_search_impl_with_ready_index(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()
    set_knowledge_index(index)
    try:
        result = await search_knowledge_base_impl("add", top_k=10)
        assert result["note"] == "ok"
        assert result["count"] >= 1
        add_results = [r for r in result["results"] if r["symbol"] == "add"]
        assert add_results
        first = add_results[0]
        assert first["file_path"] == "math_utils.py"
        assert "def add" in first["text"]

        filtered = await search_knowledge_base_impl(
            "zzzznotthere", top_k=5, score_threshold=0.5
        )
        assert filtered["results"] == []
        assert filtered["note"] == "ok"
    finally:
        set_knowledge_index(None)


@needs_chroma
async def test_search_impl_invalid_path(kb_root, provider, tmp_path):
    index = _make_index(kb_root, provider, tmp_path)
    index.rebuild()
    set_knowledge_index(index)
    try:
        result = await search_knowledge_base_impl("add", path="../escape.py")
        assert result == {"results": [], "note": "invalid_path"}
    finally:
        set_knowledge_index(None)


# ---------------------------------------------------------------------------
# contract wiring (no chroma required)
# ---------------------------------------------------------------------------


def test_create_research_agent_accepts_knowledge_index():
    from ai_dev_researcher.agents.orchestrator import create_research_agent

    sig = inspect.signature(create_research_agent)
    assert "knowledge_index" in sig.parameters
    assert sig.parameters["knowledge_index"].default is None


async def test_factory_exposes_search_knowledge_base_tool(tmp_path):
    from ai_dev_researcher.tools.factory import create_document_tools

    set_knowledge_index(None)
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    paths = WorkspacePaths(settings.sessions_root, knowledge_base_root=tmp_path / "kb")
    context = RunContext(
        run_id=uuid4(),
        session_id=uuid4(),
        question="q",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    tools = create_document_tools(context, store=object(), artifacts=object())
    search_tools = [t for t in tools if t.name == "search_knowledge_base"]
    assert search_tools
    tool = search_tools[0]
    args = tool.args
    assert "query" in args
    assert "path" in args
    assert "top_k" in args
    assert "score_threshold" in args

    result = await tool.ainvoke({"query": "anything"})
    assert result == {"results": [], "note": "indexing"}


def test_document_analyst_prompt_mentions_search_tool():
    from ai_dev_researcher.agents.prompts import DOCUMENT_ANALYST_PROMPT

    assert "search_knowledge_base" in DOCUMENT_ANALYST_PROMPT
    assert "read_knowledge_base_file" in DOCUMENT_ANALYST_PROMPT
    # "search first, then read" guidance is present.
    assert "先" in DOCUMENT_ANALYST_PROMPT and "语义定位" in DOCUMENT_ANALYST_PROMPT


def test_orchestrator_prompt_mentions_document_analyst_delegation(tmp_path):
    from ai_dev_researcher.agents.prompts import build_orchestrator_prompt

    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    paths = WorkspacePaths(settings.sessions_root, knowledge_base_root=tmp_path / "kb")
    context = RunContext(
        run_id=uuid4(),
        session_id=uuid4(),
        question="结合上传笔记分析 DeepAgents 适用边界",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    prompt = build_orchestrator_prompt(context)
    # Orchestrator must be guided to delegate doc/KB work to document-analyst.
    assert "document-analyst" in prompt
    assert "委托" in prompt
    assert "search_knowledge_base" in prompt
    assert "read_knowledge_base_file" in prompt
