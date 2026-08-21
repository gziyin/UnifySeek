from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.sessions import utc_now
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.chunk_locator import CharToLineIndex


async def list_run_documents_impl(
    *,
    context: RunContext,
    artifacts: ArtifactRepository,
) -> dict:
    items = await artifacts.get_many(context.uploaded_artifact_ids)
    return {
        "documents": [
            {
                "artifact_id": str(item.artifact_id),
                "display_name": item.display_name,
                "parse_status": item.parse_status.value,
            }
            for item in items
            if item.normalized_storage_path
        ]
    }


async def read_run_document_impl(
    *,
    context: RunContext,
    artifacts: ArtifactRepository,
    artifact_id: str,
    offset: int = 0,
    limit: int = 4000,
) -> dict:
    artifact = await artifacts.get(UUID(artifact_id))
    if artifact is None or artifact.session_id != context.session_id:
        raise ValueError("artifact not authorized")
    if artifact.artifact_id not in context.uploaded_artifact_ids:
        raise ValueError("artifact not in run snapshot")
    if not artifact.normalized_storage_path:
        raise ValueError("artifact not normalized")
    path = ensure_within_root(
        Path(artifact.normalized_storage_path),
        context.paths.sessions_root,
    )
    text = path.read_text(encoding="utf-8")
    total = len(text)
    chunk = text[offset : offset + limit]
    return {
        "artifact_id": artifact_id,
        "display_name": artifact.display_name,
        "offset": offset,
        "limit": limit,
        "total_chars": total,
        "text": chunk,
    }


async def record_document_evidence_impl(
    *,
    context: RunContext,
    store: EvidenceStore,
    artifacts: ArtifactRepository,
    artifact_id: str,
    title: str,
    excerpt: str,
    line_start: int,
    line_end: int,
    page: int | None = None,
) -> dict:
    artifact = await artifacts.get(UUID(artifact_id))
    if artifact is None or artifact.session_id != context.session_id:
        raise ValueError("artifact not authorized")
    if artifact.artifact_id not in context.uploaded_artifact_ids:
        raise ValueError("artifact not in run snapshot")
    evidence_id = await store.allocate_document_id()
    locator = f"lines {line_start}-{line_end}"
    if page is not None:
        locator = f"page {page}, {locator}"
    record = EvidenceRecord(
        id=evidence_id,
        run_id=context.run_id,
        source_type="document",
        artifact_id=artifact.artifact_id,
        evidence_level="user_document",
        title=title or artifact.display_name,
        locator=locator,
        excerpt=excerpt[:2000],
        page=page,
        line_start=line_start,
        line_end=line_end,
        retrieved_at=utc_now(),
    )
    await store.add(record)
    return {
        "evidence_id": evidence_id,
        "artifact_id": artifact_id,
        "display_name": artifact.display_name,
        "locator": locator,
        "line_start": line_start,
        "line_end": line_end,
        "page": page,
        "excerpt": store.excerpt(record, limit=240),
    }


async def search_run_documents_impl(
    *,
    context: RunContext,
    artifacts: ArtifactRepository,
    vector_store,
    query: str,
    artifact_ids: list[str] | None = None,
    top_k: int = 5,
) -> dict:
    """基于向量索引的语义检索：定位与 query 相关的文档片段（含行号范围）。

    vector_store 为 None（RAG 依赖不可用）时返回空结果，调用方回退到
    read_run_document 精确读取。
    """
    if vector_store is None:
        return {"query": query, "results": [], "note": "vector store unavailable"}
    target_ids = [str(item) for item in (artifact_ids or context.uploaded_artifact_ids)]
    if not target_ids:
        return {"query": query, "results": [], "note": "no artifacts indexed"}
    # vector_store.retrieve 内部是同步 embed + chroma query（#40），offload 到线程池，
    # 避免阻塞事件循环、保证外层超时/取消可打断。
    chunks = await asyncio.to_thread(
        vector_store.retrieve,
        query=query,
        artifact_ids=target_ids,
        top_k=top_k,
    )
    results = []
    for chunk in chunks:
        artifact = await artifacts.get(UUID(chunk.artifact_id))
        if artifact is None or artifact.normalized_storage_path is None:
            continue
        path = ensure_within_root(
            Path(artifact.normalized_storage_path),
            context.paths.sessions_root,
        )
        text = path.read_text(encoding="utf-8")
        index = CharToLineIndex.build(text)
        line_start, line_end = index.line_range(chunk.start_char, chunk.end_char)
        results.append(
            {
                "artifact_id": chunk.artifact_id,
                "display_name": artifact.display_name,
                "chunk_index": chunk.chunk_index,
                "line_start": line_start,
                "line_end": line_end,
                "score": round(chunk.score, 4),
                "text": chunk.text,
            }
        )
    return {"query": query, "results": results}
