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
        # 单个语句完成「播种 + 自增 + 返回」：aiosqlite 单语句在线程上串行执行，
        # 该语句自带写事务，天然原子（跨多条 execute 的 BEGIN/COMMIT 会被其他协程语句穿插）。
        # 首次插入时以该前缀已有最大数字后缀为种子，之后每次 +count 原子递增。
        cursor = await self._conn.execute(
            """
            INSERT INTO evidence_sequences (run_id, source_type, next_value)
            VALUES (?, ?, (
                SELECT COALESCE(MAX(CAST(substr(evidence_id, 2) AS INTEGER)), 0)
                FROM evidence
                WHERE run_id = ? AND substr(evidence_id, 1, 1) = ?
            ) + ?)
            ON CONFLICT(run_id, source_type) DO UPDATE SET next_value = next_value + ?
            RETURNING next_value
            """,
            (str(run_id), prefix, str(run_id), prefix, count, count),
        )
        row = await cursor.fetchone()
        await self._conn.commit()
        last = int(row["next_value"])
        start = last - count + 1
        return [f"{prefix}{start + i}" for i in range(count)]
