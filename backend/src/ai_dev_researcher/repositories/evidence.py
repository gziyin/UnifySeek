from __future__ import annotations

from datetime import datetime
from uuid import UUID

import aiosqlite

from ai_dev_researcher.domain.evidence import EvidenceRecord


class EvidenceRepository:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def create(self, record: EvidenceRecord) -> EvidenceRecord:
        await self._conn.execute(
            """
            INSERT INTO evidence (
                run_id, evidence_id, source_type, evidence_level, title, locator,
                canonical_url, publisher_key, excerpt, page, line_start, line_end,
                query, result_rank, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.run_id),
                record.id,
                record.source_type,
                record.evidence_level,
                record.title,
                record.locator,
                record.canonical_url,
                record.publisher_key,
                record.excerpt,
                record.page,
                record.line_start,
                record.line_end,
                record.query,
                record.result_rank,
                record.retrieved_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return record

    async def update(self, record: EvidenceRecord) -> EvidenceRecord:
        await self._conn.execute(
            """
            UPDATE evidence SET
                source_type = ?, evidence_level = ?, title = ?, locator = ?,
                canonical_url = ?, publisher_key = ?, excerpt = ?, page = ?,
                line_start = ?, line_end = ?, query = ?, result_rank = ?, retrieved_at = ?
            WHERE run_id = ? AND evidence_id = ?
            """,
            (
                record.source_type,
                record.evidence_level,
                record.title,
                record.locator,
                record.canonical_url,
                record.publisher_key,
                record.excerpt,
                record.page,
                record.line_start,
                record.line_end,
                record.query,
                record.result_rank,
                record.retrieved_at.isoformat(),
                str(record.run_id),
                record.id,
            ),
        )
        await self._conn.commit()
        return record

    async def list_for_run(self, run_id: UUID) -> list[EvidenceRecord]:
        cursor = await self._conn.execute(
            "SELECT * FROM evidence WHERE run_id = ? ORDER BY evidence_id ASC",
            (str(run_id),),
        )
        rows = await cursor.fetchall()
        return [
            EvidenceRecord(
                id=row["evidence_id"],
                run_id=UUID(row["run_id"]),
                source_type=row["source_type"],
                evidence_level=row["evidence_level"],
                title=row["title"],
                locator=row["locator"],
                canonical_url=row["canonical_url"],
                publisher_key=row["publisher_key"],
                excerpt=row["excerpt"],
                page=row["page"],
                line_start=row["line_start"],
                line_end=row["line_end"],
                query=row["query"],
                result_rank=row["result_rank"],
                retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
            )
            for row in rows
        ]

    async def allocate_ids(
        self,
        run_id: UUID,
        *,
        web_count: int = 0,
        document_count: int = 0,
    ) -> tuple[list[str], list[str]]:
        existing = await self.list_for_run(run_id)
        web_ids = [item.id for item in existing if item.id.startswith("S")]
        doc_ids = [item.id for item in existing if item.id.startswith("D")]
        next_s = len(web_ids) + 1
        next_d = len(doc_ids) + 1
        allocated_web = [f"S{next_s + i}" for i in range(web_count)]
        allocated_doc = [f"D{next_d + i}" for i in range(document_count)]
        return allocated_web, allocated_doc
