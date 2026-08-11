from __future__ import annotations

from pathlib import Path
from uuid import UUID

import aiosqlite
import pytest

from ai_dev_researcher.core.errors import RunConflictError
from ai_dev_researcher.domain.runs import ResearchRequest, RunStatus
from ai_dev_researcher.domain.sessions import Session
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.run_service import RunService
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.storage.paths import WorkspacePaths


async def _noop_executor(run_id: UUID) -> None:
    return None


def _noop_factory():
    return _noop_executor


async def _build_service(
    tmp_path: Path,
) -> tuple[RunService, aiosqlite.Connection]:
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    paths = WorkspacePaths(tmp_path / "sessions")
    publisher = EventPublisher(EventRepository(conn))
    task_manager = TaskManager(_noop_factory)
    service = RunService(
        sessions=SessionRepository(conn),
        runs=RunRepository(conn),
        artifacts=ArtifactRepository(conn),
        paths=paths,
        publisher=publisher,
        task_manager=task_manager,
    )
    return service, conn


def _question() -> ResearchRequest:
    return ResearchRequest(question="深度智能体边界分析")


@pytest.mark.asyncio
async def test_create_run_supersedes_stale_cancelling_run(tmp_path: Path):
    """issue #29：旧 run 卡在 cancelling 时，create_run 收敛其为 cancelled 并允许新建。"""
    service, conn = await _build_service(tmp_path)
    session = await service._sessions.create(Session())

    first = await service.create_run(session.session_id, _question())
    # 模拟用户点取消后仍卡在 cancelling（异步取消未收敛的竞态窗口）。
    await service._runs.update_status(first.run_id, RunStatus.RUNNING, started=True)
    await service._runs.update_status(first.run_id, RunStatus.CANCELLING, cancel_requested=True)

    second = await service.create_run(session.session_id, _question())
    assert second.run_id != first.run_id

    # 旧 run 已收敛为终态 cancelled。
    first_after = await service._runs.get(first.run_id)
    assert first_after is not None
    assert first_after.status == RunStatus.CANCELLED
    assert first_after.finished_at is not None
    await conn.close()


@pytest.mark.asyncio
async def test_create_run_still_rejects_running_active(tmp_path: Path):
    """正常研究中的 run（running）不能被覆盖，仍抛 RUN_ACTIVE 409。"""
    service, conn = await _build_service(tmp_path)
    session = await service._sessions.create(Session())

    first = await service.create_run(session.session_id, _question())
    await service._runs.update_status(first.run_id, RunStatus.RUNNING, started=True)

    with pytest.raises(RunConflictError):
        await service.create_run(session.session_id, _question())
    await conn.close()


@pytest.mark.asyncio
async def test_create_run_second_after_success_ok(tmp_path: Path):
    """succeeded 终态后立即再研究不报 409（回归基线）。"""
    service, conn = await _build_service(tmp_path)
    session = await service._sessions.create(Session())

    first = await service.create_run(session.session_id, _question())
    await service._runs.update_status(first.run_id, RunStatus.RUNNING, started=True)
    await service._runs.update_status(first.run_id, RunStatus.SUCCEEDED, finished=True)

    second = await service.create_run(session.session_id, _question())
    assert second.run_id != first.run_id
    await conn.close()
