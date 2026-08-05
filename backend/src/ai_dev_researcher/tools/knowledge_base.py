from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.errors import KnowledgeBaseError
from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.sessions import utc_now
from ai_dev_researcher.services.evidence_store import EvidenceStore

if TYPE_CHECKING:
    from ai_dev_researcher.storage.knowledge_index import KbChunk, KnowledgeIndex

logger = logging.getLogger(__name__)

SUPPORTED_KB_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml"}

# Deprecated compatibility API for out-of-domain tests. Production wiring now
# passes the index explicitly through factory/executor; new code should not
# call set/get_knowledge_index.
_kb_index: "KnowledgeIndex | None" = None


def set_knowledge_index(index) -> None:
    """Legacy test-only locator; use explicit DI in application code."""
    global _kb_index  # noqa: PLW0603
    _kb_index = index


def get_knowledge_index():
    """Legacy test-only locator; prefer explicit DI."""
    return _kb_index


async def search_knowledge_base_impl(
    query: str,
    path: str | None = None,
    top_k: int = 10,
    score_threshold: float = 0.0,
    knowledge_index: "KnowledgeIndex | None" = None,
) -> dict:
    """Semantically search the local knowledge base (WP-A contract).

    Returns ``{"results": [], "note": "indexing"}`` when the index is not
    ready yet, so agents get a gentle hint instead of a hard error.
    """
    index = knowledge_index if knowledge_index is not None else get_knowledge_index()
    if index is None or not index.is_ready:
        return {"results": [], "note": "indexing"}
    safe_path: str | None = None
    if path is not None:
        safe_path = str(path).strip().replace("\\", "/")
        if (
            not safe_path
            or safe_path.startswith("/")
            or safe_path.startswith("..")
            or "://" in safe_path
            or (len(safe_path) >= 2 and safe_path[1] == ":")
        ):
            return {"results": [], "note": "invalid_path"}
    top_k = max(1, min(int(top_k), 50))
    score_threshold = max(0.0, float(score_threshold))
    try:
        chunks = index.retrieve(
            query=query,
            path=safe_path,
            top_k=top_k,
            score_threshold=score_threshold,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge base search failed: %s", exc)
        return {"results": [], "note": "error"}
    return {
        "results": [chunk.to_dict() for chunk in chunks],
        "count": len(chunks),
        "note": "ok",
    }

def _kb_root(context: RunContext) -> Path:
    root = context.settings.knowledge_base_root
    if root is None:
        raise KnowledgeBaseError("knowledge base is not configured")
    return root.resolve()


def _safe_relative(context: RunContext, raw: str) -> str:
    """Validate a user-supplied relative path against the KB root."""
    value = raw.strip().replace("\\", "/")
    if not value or value.startswith("/") or value.startswith(".."):
        raise KnowledgeBaseError("invalid knowledge base path")
    if "://" in value or (len(value) >= 2 and value[1] == ":"):
        raise KnowledgeBaseError("absolute or URL path rejected")
    # Reject any remaining parent traversal.
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise KnowledgeBaseError("invalid knowledge base path")
    return value


async def list_knowledge_base_entries_impl(
    *,
    context: RunContext,
    path: str = ".",
) -> dict:
    root = _kb_root(context)
    relative = "." if path in {"", "."} else _safe_relative(context, path)
    target = ensure_within_root(root / relative, root)
    if not target.exists() or not target.is_dir():
        raise KnowledgeBaseError(f"not a directory: {path}")
    entries: list[dict] = []
    for child in sorted(target.iterdir()):
        if child.is_dir():
            entries.append({"name": child.name, "type": "dir", "path": f"{relative.rstrip('/')}/{child.name}".lstrip("./")})
        elif child.suffix.lower() in SUPPORTED_KB_EXTENSIONS:
            entries.append({"name": child.name, "type": "file", "path": f"{relative.rstrip('/')}/{child.name}".lstrip("./")})
    return {"root": str(root), "path": relative, "entries": entries}


async def read_knowledge_base_file_impl(
    *,
    context: RunContext,
    path: str,
    offset: int = 0,
    limit: int = 4000,
) -> dict:
    root = _kb_root(context)
    relative = _safe_relative(context, path)
    target = ensure_within_root(root / relative, root)
    if not target.exists() or not target.is_file():
        raise KnowledgeBaseError(f"file not found: {path}")
    if target.suffix.lower() not in SUPPORTED_KB_EXTENSIONS:
        raise KnowledgeBaseError(f"unsupported file type: {target.suffix}")
    text = target.read_text(encoding="utf-8", errors="replace")
    total = len(text)
    chunk = text[offset : offset + limit]
    return {
        "path": relative,
        "display_name": target.name,
        "offset": offset,
        "limit": limit,
        "total_chars": total,
        "text": chunk,
    }


async def record_knowledge_base_evidence_impl(
    *,
    context: RunContext,
    store: EvidenceStore,
    path: str,
    title: str,
    excerpt: str,
    line_start: int,
    line_end: int,
) -> dict:
    root = _kb_root(context)
    relative = _safe_relative(context, path)
    # Verify the referenced file exists before recording evidence.
    target = ensure_within_root(root / relative, root)
    if not target.exists() or not target.is_file():
        raise KnowledgeBaseError(f"file not found: {path}")
    evidence_id = await store.allocate_knowledge_base_id()
    locator = f"kb:{relative} lines {line_start}-{line_end}"
    record = EvidenceRecord(
        id=evidence_id,
        run_id=context.run_id,
        source_type="knowledge_base",
        evidence_level="first_party",
        title=title or target.name,
        locator=locator,
        canonical_url=None,
        publisher_key=None,
        excerpt=excerpt[:2000],
        line_start=line_start,
        line_end=line_end,
        retrieved_at=utc_now(),
    )
    await store.add(record)
    return {
        "evidence_id": evidence_id,
        "locator": locator,
        "path": relative,
        "line_start": line_start,
        "line_end": line_end,
        "excerpt": store.excerpt(record, limit=240),
    }
