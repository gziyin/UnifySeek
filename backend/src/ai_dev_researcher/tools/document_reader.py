from __future__ import annotations

from pathlib import Path
from uuid import UUID

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.sessions import utc_now
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.services.evidence_store import EvidenceStore


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
        "locator": locator,
        "excerpt": store.excerpt(record, limit=240),
    }
