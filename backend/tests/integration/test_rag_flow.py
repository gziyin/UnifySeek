from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.storage.vector_store import VectorStore
from ai_dev_researcher.tools.document_reader import search_run_documents_impl


class FakeEmbeddingProvider:
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


@pytest.fixture
async def env(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "workspace")
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    paths = WorkspacePaths(settings.sessions_root)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    session = await SessionRepository(conn).create()
    session_id = session.session_id
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    artifacts = ArtifactRepository(conn)
    evidence_repo = EvidenceRepository(conn)
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=evidence_repo,
        paths=paths,
    )
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="表格中的参数是多少",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    vector_store = VectorStore(
        persist_dir=tmp_path / "chroma",
        embedding_provider=FakeEmbeddingProvider(),
    )
    yield context, artifacts, store, vector_store
    await conn.close()


async def test_search_run_documents_returns_empty_when_no_vector_store(env):
    context, artifacts, _store, _vs = env
    result = await search_run_documents_impl(
        context=context,
        artifacts=artifacts,
        vector_store=None,
        query="anything",
    )
    assert result["results"] == []
    assert "unavailable" in result["note"]


async def test_search_run_documents_finds_indexed_table_text(env):
    context, artifacts, store, vector_store = env
    if not vector_store.available:
        pytest.skip("chromadb not installed")

    # 模拟上传文档：生成一个带表格内容的归一化文本并建立索引。
    artifact_id = uuid4()
    normalized_path = context.paths.normalized_path(context.session_id, artifact_id)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    table_text = (
        "[PAGE 1]\n"
        "模型参数表\n"
        "temperature: 0.7\n"
        "max_tokens: 2048\n"
        "top_p: 0.9\n"
    )
    normalized_path.write_text(table_text, encoding="utf-8")
    artifact = Artifact(
        artifact_id=artifact_id,
        session_id=context.session_id,
        kind=ArtifactKind.UPLOAD,
        display_name="report-table.pdf",
        mime_type="application/pdf",
        size_bytes=len(table_text.encode("utf-8")),
        parse_status=ParseStatus.PARSED,
        normalized_storage_path=str(normalized_path),
    )
    await artifacts.create(artifact)
    context.uploaded_artifact_ids.append(artifact_id)
    vector_store.index_document(artifact_id=str(artifact_id), text=table_text)

    result = await search_run_documents_impl(
        context=context,
        artifacts=artifacts,
        vector_store=vector_store,
        query="temperature",
        top_k=3,
    )
    assert result["results"]
    first = result["results"][0]
    assert first["artifact_id"] == str(artifact_id)
    assert first["line_start"] >= 1
    assert "temperature" in first["text"]
    assert first["score"] > 0
