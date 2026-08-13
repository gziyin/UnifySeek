from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus
from ai_dev_researcher.domain.sessions import Session
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db


@pytest.mark.asyncio
async def test_list_filters_empty_sessions_and_orders_by_updated_at(tmp_path: Path):
    """#30/#32：list() 只返回有 run 的会话，并按最近活动（updated_at）倒序。"""
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    sessions = SessionRepository(conn)
    runs = RunRepository(conn)

    # A/B 有 run；C 无 run 且 display_name=None（空会话，应被过滤）
    a = await sessions.create(Session(display_name="alpha"))
    b = await sessions.create(Session(display_name="beta"))
    c = await sessions.create(Session())

    base = datetime.now(timezone.utc)
    await runs.create(
        Run(
            session_id=a.session_id,
            status=RunStatus.SUCCEEDED,
            request=ResearchRequest(question="a"),
            created_at=base,
        )
    )
    await runs.create(
        Run(
            session_id=b.session_id,
            status=RunStatus.SUCCEEDED,
            request=ResearchRequest(question="b"),
            created_at=base + timedelta(seconds=1),
        )
    )

    # 确定性排序：直接设定 updated_at，B 比 A 最近活动 → B 排在前。
    await conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
        (base.isoformat(), str(a.session_id)),
    )
    await conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
        ((base + timedelta(seconds=5)).isoformat(), str(b.session_id)),
    )
    await conn.commit()

    items = await sessions.list()
    ids = [s.session_id for s in items]
    assert c.session_id not in ids  # 空会话被过滤
    assert ids == [b.session_id, a.session_id]  # 最近活动在前
    assert {s.display_name for s in items} == {"alpha", "beta"}
    await conn.close()


@pytest.mark.asyncio
async def test_list_filters_display_name_none_empty_session(tmp_path: Path):
    """无 run 且 display_name=None 的会话不进入历史列表（#32 无效记录）。"""
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    sessions = SessionRepository(conn)
    runs = RunRepository(conn)

    empty = await sessions.create(Session())  # 无 run
    has_run = await sessions.create(Session(display_name="named"))
    await runs.create(
        Run(
            session_id=has_run.session_id,
            status=RunStatus.SUCCEEDED,
            request=ResearchRequest(question="q"),
            created_at=datetime.now(timezone.utc),
        )
    )

    items = await sessions.list()
    ids = [s.session_id for s in items]
    assert ids == [has_run.session_id]
    assert empty.session_id not in ids
    await conn.close()
