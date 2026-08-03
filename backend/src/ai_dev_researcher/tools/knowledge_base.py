from __future__ import annotations

from pathlib import Path

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.errors import KnowledgeBaseError
from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.sessions import utc_now
from ai_dev_researcher.services.evidence_store import EvidenceStore

SUPPORTED_KB_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml"}


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
