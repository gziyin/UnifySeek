from __future__ import annotations

from datetime import datetime
from uuid import UUID

import aiosqlite

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.repositories.sqlite import run_atomic


class EvidenceRepository:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def create(self, record: EvidenceRecord) -> EvidenceRecord:
        await self._conn.execute(
            """
            INSERT INTO evidence (
                run_id, evidence_id, artifact_id, source_type, evidence_level, title, locator,
                canonical_url, publisher_key, excerpt, page, line_start, line_end,
                query, result_rank, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.run_id),
                record.id,
                str(record.artifact_id) if record.artifact_id else None,
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
                artifact_id = ?, source_type = ?, evidence_level = ?, title = ?, locator = ?,
                canonical_url = ?, publisher_key = ?, excerpt = ?, page = ?,
                line_start = ?, line_end = ?, query = ?, result_rank = ?, retrieved_at = ?
            WHERE run_id = ? AND evidence_id = ?
            """,
            (
                str(record.artifact_id) if record.artifact_id else None,
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
                artifact_id=UUID(row["artifact_id"]) if row["artifact_id"] else None,
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

    async def delete_by_run_ids(self, run_ids: list[UUID]) -> int:
        """Delete evidence rows for the given runs. Returns the number removed."""
        if not run_ids:
            return 0
        placeholders = ",".join("?" for _ in run_ids)
        cursor = await self._conn.execute(
            f"DELETE FROM evidence WHERE run_id IN ({placeholders})",
            [str(item) for item in run_ids],
        )
        await self._conn.commit()
        return cursor.rowcount

    async def allocate_ids(
        self,
        run_id: UUID,
        *,
        web_count: int = 0,
        document_count: int = 0,
        knowledge_base_count: int = 0,
    ) -> tuple[list[str], list[str], list[str]]:
        """Atomically reserve evidence ID ranges per prefix for a run.

        基于 evidence_sequences 表以单条 ``INSERT..ON CONFLICT..RETURNING`` 原子自增预留，
        保证并发分配互不重复（run b87b0077 曾因「读数 + 落库分离」产出重复 K ID）。
        ID 空洞可接受：sequence 只递增，不复用已预留或已删除的编号。
        """
        web = await self._reserve_ids(run_id, "S", web_count)
        doc = await self._reserve_ids(run_id, "D", document_count)
        kb = await self._reserve_ids(run_id, "K", knowledge_base_count)
        return web, doc, kb

    async def _reserve_ids(
        self, run_id: UUID, prefix: str, count: int
    ) -> list[str]:
        if count <= 0:
            return []

        sql = """
            INSERT INTO evidence_sequences (run_id, source_type, next_value)
            VALUES (?, ?, (
                SELECT COALESCE(MAX(CAST(substr(evidence_id, 2) AS INTEGER)), 0)
                FROM evidence
                WHERE run_id = ? AND substr(evidence_id, 1, 1) = ?
            ) + ?)
            ON CONFLICT(run_id, source_type) DO UPDATE SET next_value = next_value + ?
            RETURNING next_value
        """
        params = (str(run_id), prefix, str(run_id), prefix, count, count)

        def work() -> int:
            cursor = self._conn._conn.execute(sql, params)
            try:
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("evidence_sequences RETURNING returned no row")
                last_value = int(row["next_value"])
                self._conn._conn.commit()
                return last_value
            finally:
                cursor.close()

        try:
            last = await run_atomic(self._conn, work)
        except Exception:
            await self._conn.rollback()
            raise

        start = last - count + 1
        return [f"{prefix}{start + i}" for i in range(count)]
