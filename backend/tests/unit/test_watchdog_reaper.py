"""#40 回归：TaskManager 硬超时 / shutdown 超时 / stale run 回收器。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus
from ai_dev_researcher.domain.sessions import Session
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.run_guard import converge_stuck_run, reap_stale_runs
from ai_dev_researcher.services.task_manager import TaskManager


async def _never_returns_executor(run_id: UUID) -> None:
    await asyncio.Event().wait()


async def _slow_unwind_executor(run_id: UUID) -> None:
    """取消后收敛路径很慢（可打断但耗时）：shutdown 需靠超时兜底不挂死。"""
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        await asyncio.sleep(5)
        raise


@pytest.fixture
async def repo(tmp_path: Path):
    """隔离 DB + 必关连接：即使断言失败也不留悬挂 aiosqlite 连接（否则进程退出挂死）。"""
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    session = await SessionRepository(conn).create()
    try:
        yield RunRepository(conn), session
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_hard_timeout_invokes_on_stuck(repo):
    """TaskManager 硬超时：executor 永不返回时 wait_for 触发强制结束，任务不残留。"""
    tm = TaskManager(lambda: _never_returns_executor, on_stuck=None, shutdown_timeout=1.0)
    run_id = uuid4()
    await tm.start_run(run_id, timeout=0.05)
    await asyncio.sleep(0.2)
    assert await tm.has_live_task(run_id) is False
    await tm.shutdown()


@pytest.mark.asyncio
async def test_hard_timeout_converges_run_terminal(repo):
    """硬超时收敛：on_stuck 把 run 置为 INTERRUPTED/RUN_TIMEOUT，并发布 run.failed。"""
    runs_repo, session = repo
    conn = runs_repo._conn
    publisher = EventPublisher(EventRepository(conn))

    run = Run(session_id=session.session_id, request=ResearchRequest(question="测试问题"))
    await runs_repo.create(run)
    await runs_repo.update_status(run.run_id, RunStatus.RUNNING, started=True)

    stuck = asyncio.Event()

    async def on_stuck(run_id: UUID) -> None:
        await converge_stuck_run(runs_repo, publisher, run_id)
        stuck.set()

    tm = TaskManager(lambda: _never_returns_executor, on_stuck=on_stuck, shutdown_timeout=1.0)
    await tm.start_run(run.run_id, timeout=0.05)
    await asyncio.wait_for(stuck.wait(), timeout=2)

    updated = await runs_repo.get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.INTERRUPTED
    assert updated.error_code == "RUN_TIMEOUT"
    assert updated.finished_at is not None

    events = await EventRepository(conn).list_after(run.run_id, 0)
    assert any(e.type == "run.failed" for e in events)


@pytest.mark.asyncio
async def test_shutdown_times_out_instead_of_hanging(repo):
    """shutdown 超时兜底：收敛慢的任务也要在时限内返回，进程不被拖死（#40）。"""
    tm = TaskManager(lambda: _slow_unwind_executor, shutdown_timeout=0.2)
    run_id = uuid4()
    await tm.start_run(run_id)
    await asyncio.sleep(0.05)  # 让 executor 进入 sleep(30)

    started = time.monotonic()
    await tm.shutdown()
    elapsed = time.monotonic() - started
    assert elapsed < 2.0  # 远小于收敛路径的 5s


@pytest.mark.asyncio
async def test_reap_stale_runs_reclaims_dead_but_not_live(repo):
    """回收器口径：无 live task 的 active run 被回收为 INTERRUPTED；有 live task 的不回收。"""
    runs_repo, session = repo
    publisher = EventPublisher(EventRepository(runs_repo._conn))

    dead = Run(session_id=session.session_id, request=ResearchRequest(question="dead run"))
    await runs_repo.create(dead)
    await runs_repo.update_status(dead.run_id, RunStatus.RUNNING, started=True)

    live = Run(session_id=session.session_id, request=ResearchRequest(question="live run"))
    await runs_repo.create(live)
    await runs_repo.update_status(live.run_id, RunStatus.RUNNING, started=True)

    tm = TaskManager(lambda: _never_returns_executor)
    await tm.start_run(live.run_id)

    reclaimed = await reap_stale_runs(runs_repo, publisher, tm)

    assert reclaimed == 1
    dead_after = await runs_repo.get(dead.run_id)
    assert dead_after is not None
    assert dead_after.status == RunStatus.INTERRUPTED
    assert dead_after.error_code == "STALE_RECLAIMED"

    live_after = await runs_repo.get(live.run_id)
    assert live_after is not None
    assert live_after.status == RunStatus.RUNNING  # live task 存活，不回收

    await tm.shutdown()


@pytest.mark.asyncio
async def test_converge_stuck_run_is_noop_when_terminal(repo):
    """硬超时收敛：run 已是终态时不覆盖。"""
    runs_repo, session = repo
    publisher = EventPublisher(EventRepository(runs_repo._conn))
    run = Run(session_id=session.session_id, request=ResearchRequest(question="测试问题"))
    await runs_repo.create(run)
    await runs_repo.update_status(run.run_id, RunStatus.RUNNING, started=True)
    await runs_repo.update_status(run.run_id, RunStatus.SUCCEEDED, finished=True)

    await converge_stuck_run(runs_repo, publisher, run.run_id)

    updated = await runs_repo.get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_list_active_runs_returns_only_active(repo):
    """list_active_runs 只返回 PENDING/RUNNING/CANCELLING（回收器输入）。"""
    runs_repo, session = repo
    active = Run(session_id=session.session_id, request=ResearchRequest(question="active"))
    done = Run(session_id=session.session_id, request=ResearchRequest(question="done"))
    await runs_repo.create(active)
    await runs_repo.create(done)
    await runs_repo.update_status(active.run_id, RunStatus.RUNNING, started=True)
    await runs_repo.update_status(done.run_id, RunStatus.RUNNING, started=True)
    await runs_repo.update_status(done.run_id, RunStatus.SUCCEEDED, finished=True)

    active_ids = {run.run_id for run in await runs_repo.list_active_runs()}
    assert active.run_id in active_ids
    assert done.run_id not in active_ids
