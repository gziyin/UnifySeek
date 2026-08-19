from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.storage.paths import WorkspacePaths


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Line ranges overlap; an empty range (no line info) matches anything in the file."""
    a_empty = a_end < a_start or (a_start <= 0 and a_end <= 0)
    b_empty = b_end < b_start or (b_start <= 0 and b_end <= 0)
    if a_empty or b_empty:
        return True
    return max(a_start, b_start) <= min(a_end, b_end)


@dataclass
class KbCandidate:
    """A search hit that authorizes a same-run KB record (batch-A-evidence-kb-gates)."""

    path: str
    line_start: int
    line_end: int
    score: float
    evidence_id: str | None = None


class KbCandidateRegistry:
    """Run-scoped KB candidate registry.

    ``search_knowledge_base`` hits whose score passes the relevance threshold are
    registered here (by the factory tool wrapper). ``record_knowledge_base_evidence``
    may only record a candidate that was searched in the SAME run (same path and
    overlapping line range), so prefetch (which calls the impl directly, bypassing the
    factory wrapper) can never authorize a record. Duplicate records of the same
    candidate are idempotent: they return the already-recorded evidence_id.
    """

    def __init__(self, *, score_threshold: float = 0.3):
        self._score_threshold = score_threshold
        self._candidates: list[KbCandidate] = []

    def register(
        self, path: str, line_start: int, line_end: int, score: float
    ) -> None:
        if not path:
            return
        if score < self._score_threshold:
            return
        if self._find(path, line_start, line_end) is None:
            self._candidates.append(
                KbCandidate(
                    path=path,
                    line_start=line_start,
                    line_end=line_end,
                    score=score,
                )
            )

    def _find(self, path: str, line_start: int, line_end: int) -> KbCandidate | None:
        for candidate in self._candidates:
            if candidate.path == path and _ranges_overlap(
                candidate.line_start, candidate.line_end, line_start, line_end
            ):
                return candidate
        return None

    def matches(self, path: str, line_start: int, line_end: int) -> bool:
        return self._find(path, line_start, line_end) is not None

    def recorded_evidence_id(
        self, path: str, line_start: int, line_end: int
    ) -> str | None:
        candidate = self._find(path, line_start, line_end)
        return candidate.evidence_id if candidate is not None else None

    def mark_recorded(
        self, path: str, line_start: int, line_end: int, evidence_id: str
    ) -> None:
        candidate = self._find(path, line_start, line_end)
        if candidate is not None:
            candidate.evidence_id = evidence_id
        else:
            self._candidates.append(
                KbCandidate(
                    path=path,
                    line_start=line_start,
                    line_end=line_end,
                    score=self._score_threshold,
                    evidence_id=evidence_id,
                )
            )

    @property
    def recorded_count(self) -> int:
        return sum(1 for c in self._candidates if c.evidence_id)


class EvidenceStore:
    """Per-run evidence ledger with transactional ID allocation."""

    def __init__(
        self,
        *,
        run_id: UUID,
        session_id: UUID,
        evidence_repo: EvidenceRepository,
        paths: WorkspacePaths,
    ):
        self._run_id = run_id
        self._session_id = session_id
        self._repo = evidence_repo
        self._paths = paths
        self._lock = asyncio.Lock()
        self._body_dir = paths.evidence_dir(session_id, run_id)

    async def allocate_web_id(self) -> str:
        async with self._lock:
            web_ids, _, _ = await self._repo.allocate_ids(self._run_id, web_count=1)
            return web_ids[0]

    async def allocate_document_id(self) -> str:
        async with self._lock:
            _, doc_ids, _ = await self._repo.allocate_ids(self._run_id, document_count=1)
            return doc_ids[0]

    async def allocate_knowledge_base_id(self) -> str:
        async with self._lock:
            _, _, kb_ids = await self._repo.allocate_ids(
                self._run_id, knowledge_base_count=1
            )
            return kb_ids[0]

    async def add(self, record: EvidenceRecord) -> EvidenceRecord:
        self._body_dir.mkdir(parents=True, exist_ok=True)
        body_path = self._body_dir / f"{record.id}.txt"
        body_path.write_text(record.excerpt, encoding="utf-8")
        existing = await self.get(record.id)
        if existing is None:
            return await self._repo.create(record)
        return await self._repo.update(record)

    async def list_for_run(self) -> list[EvidenceRecord]:
        return await self._repo.list_for_run(self._run_id)

    async def get(self, evidence_id: str) -> EvidenceRecord | None:
        items = await self.list_for_run()
        return next((item for item in items if item.id == evidence_id), None)

    def excerpt(self, record: EvidenceRecord, *, limit: int = 500) -> str:
        text = record.excerpt
        if len(text) <= limit:
            return text
        return text[:limit] + "…"
