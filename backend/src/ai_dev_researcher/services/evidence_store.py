from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.storage.paths import WorkspacePaths


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
            web_ids, _ = await self._repo.allocate_ids(self._run_id, web_count=1)
            return web_ids[0]

    async def allocate_document_id(self) -> str:
        async with self._lock:
            _, doc_ids = await self._repo.allocate_ids(self._run_id, document_count=1)
            return doc_ids[0]

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
