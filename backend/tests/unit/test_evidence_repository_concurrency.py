"""A1：Evidence ID 原子分配（batch-A-evidence-kb-gates）。

run b87b0077 实测并发 K 分配对同一 run 产出重复 K ID：allocate_ids 以「已存在数量 + 1」
推导下一个 ID，且分配读取与后续落库分离，并发时两个任务可能读到同一空的 evidence 表
→ 都算出 K1。修复采用数据库级原子预留（evidence_sequences 表，允许 ID 空洞）。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db, run_atomic


@pytest.fixture
async def repo(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    run_id = uuid4()
    try:
        yield EvidenceRepository(conn), run_id
    finally:
        await conn.close()


async def test_allocate_ids_sequential_is_contiguous_and_gap_tolerant(repo):
    """非并发顺序分配：ID 连续递增；删除造成空洞后不复用已占用 ID。"""
    evidence_repo, run_id = repo

    web, _, _ = await evidence_repo.allocate_ids(run_id, web_count=3)
    assert web == ["S1", "S2", "S3"]
    _, doc, kb = await evidence_repo.allocate_ids(
        run_id, document_count=2, knowledge_base_count=2
    )
    assert doc == ["D1", "D2"]
    assert kb == ["K1", "K2"]

    # 制造空洞（S2 删除）后再分配：不回到 S2，也不碰撞已存在的 S3。
    await evidence_repo._conn.execute(
        "DELETE FROM evidence WHERE run_id = ? AND evidence_id = ?",
        (str(run_id), "S2"),
    )
    await evidence_repo._conn.commit()
    web2, _, _ = await evidence_repo.allocate_ids(run_id, web_count=1)
    assert web2 == ["S4"]


async def test_concurrent_allocate_ids_never_duplicates(repo):
    """并发 allocate_ids：同一 DB/run 下为 S/D/K 各分配多次，ID 必须全局唯一。"""
    evidence_repo, run_id = repo
    workers = 8
    allocations_per_worker = 3
    barrier = asyncio.Barrier(workers)

    async def worker(_idx: int) -> list[tuple[str, str, str]]:
        await barrier.wait()
        out: list[tuple[str, str, str]] = []
        for _ in range(allocations_per_worker):
            web, doc, kb = await evidence_repo.allocate_ids(
                run_id,
                web_count=1,
                document_count=1,
                knowledge_base_count=1,
            )
            out.append((web[0], doc[0], kb[0]))
        return out

    results = await asyncio.gather(*(worker(i) for i in range(workers)))

    flat = [
        item
        for worker_result in results
        for triple in worker_result
        for item in triple
    ]
    # 每个调用同时分配 S/D/K 各 1：三种前缀都各自出现 workers * allocations_per_worker 次。
    for prefix in ("S", "D", "K"):
        ids = {item for item in flat if item.startswith(prefix)}
        assert len(ids) == workers * allocations_per_worker
    assert len(set(flat)) == len(flat)

@pytest.mark.asyncio
async def test_run_atomic_keeps_returning_finalize_and_commit_in_one_worker(repo):
    """A RETURNING statement must be finalized before another commit can run."""
    evidence_repo, run_id = repo
    events = EventRepository(evidence_repo._conn)
    entered = threading.Event()
    release = threading.Event()

    def reserve_work() -> int:
        cursor = evidence_repo._conn._conn.execute(
            """
            INSERT INTO evidence_sequences (run_id, source_type, next_value)
            VALUES (?, ?, 1)
            RETURNING next_value
            """,
            (str(run_id), "S"),
        )
        try:
            row = cursor.fetchone()
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test worker was not released")
            return int(row["next_value"])
        finally:
            cursor.close()
            evidence_repo._conn._conn.commit()

    reserve_task = asyncio.create_task(run_atomic(evidence_repo._conn, reserve_work))
    await asyncio.to_thread(entered.wait)
    event_task = asyncio.create_task(
        events.append(
            session_id=uuid4(),
            run_id=run_id,
            event_type="heartbeat",
            actor="system",
            payload={"source": "queued"},
        )
    )
    await asyncio.sleep(0)
    release.set()

    reserved, event = await asyncio.gather(reserve_task, event_task)
    assert reserved == 1
    assert event.seq == 1


@pytest.mark.asyncio
async def test_allocate_ids_interleaves_with_committing_repositories_without_operational_error(
    repo,
):
    """Evidence ID allocation remains safe beside event and run commits."""
    evidence_repo, _ = repo
    conn = evidence_repo._conn
    session = await SessionRepository(conn).create()
    run = Run(session_id=session.session_id, request=ResearchRequest(question="mixed"))
    runs = RunRepository(conn)
    await runs.create(run)
    events = EventRepository(conn)

    async def allocate():
        return await evidence_repo.allocate_ids(
            run.run_id,
            web_count=1,
            document_count=1,
            knowledge_base_count=1,
        )

    async def append_event():
        return await events.append(
            session_id=session.session_id,
            run_id=run.run_id,
            event_type="heartbeat",
            actor="system",
            payload={"source": "mixed"},
        )

    async def update_run():
        return await runs.update_status(run.run_id, RunStatus.RUNNING, started=True)

    results = await asyncio.gather(
        *(allocate() for _ in range(20)),
        *(append_event() for _ in range(20)),
        *(update_run() for _ in range(20)),
    )

    allocations = results[:20]
    for prefix_index, prefix in enumerate(("S", "D", "K")):
        ids = {allocation[prefix_index][0] for allocation in allocations}
        assert len(ids) == 20
        assert all(item.startswith(prefix) for item in ids)
