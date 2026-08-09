from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db


@pytest.fixture
async def env(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    session = await SessionRepository(conn).create()
    session_id = session.session_id
    runs = RunRepository(conn)
    yield session_id, runs
    await conn.close()


def _run(session_id, created_at: datetime) -> Run:
    return Run(
        session_id=session_id,
        status=RunStatus.SUCCEEDED,
        request=ResearchRequest(question=f"question {created_at}"),
        created_at=created_at,
    )


async def test_list_for_session_orders_newest_first(env):
    session_id, runs = env
    base = datetime.now(timezone.utc)
    await runs.create(_run(session_id, base))
    await runs.create(_run(session_id, base + timedelta(seconds=1)))
    await runs.create(_run(session_id, base + timedelta(seconds=2)))

    listed = await runs.list_for_session(session_id)

    assert len(listed) == 3
    assert [r.created_at for r in listed] == sorted(
        [r.created_at for r in listed], reverse=True
    )
    assert listed[0].created_at == base + timedelta(seconds=2)
    assert listed[2].created_at == base


async def test_list_for_session_respects_limit_and_offset(env):
    session_id, runs = env
    base = datetime.now(timezone.utc)
    for i in range(5):
        await runs.create(_run(session_id, base + timedelta(seconds=i)))

    limited = await runs.list_for_session(session_id, limit=2)
    assert len(limited) == 2
    assert limited[0].created_at == base + timedelta(seconds=4)
    assert limited[1].created_at == base + timedelta(seconds=3)

    paged = await runs.list_for_session(session_id, limit=2, offset=2)
    assert [r.created_at for r in paged] == [
        base + timedelta(seconds=2),
        base + timedelta(seconds=1),
    ]


async def test_list_for_session_default_limit_covers_small_batch(env):
    session_id, runs = env
    base = datetime.now(timezone.utc)
    for i in range(3):
        await runs.create(_run(session_id, base + timedelta(seconds=i)))

    listed = await runs.list_for_session(session_id)
    assert len(listed) == 3
    assert all(r.session_id == session_id for r in listed)


async def test_list_for_session_empty_session_returns_empty(env):
    session_id, runs = env
    listed = await runs.list_for_session(session_id)
    assert listed == []
