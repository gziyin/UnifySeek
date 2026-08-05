from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db


@pytest.fixture
async def repo(tmp_path: Path):
    conn = await connect(str(tmp_path / "events.db"))
    await init_db(conn)
    yield EventRepository(conn), uuid4()
    await conn.close()


@pytest.mark.asyncio
async def test_concurrent_appends_assign_unique_contiguous_seq(repo):
    events, run_id = repo

    for batch in range(3):
        await asyncio.gather(
            *[
                events.append(
                    session_id=uuid4(),
                    run_id=run_id,
                    event_type="heartbeat",
                    actor="system",
                    payload={"batch": batch, "index": index},
                )
                for index in range(30)
            ]
        )
        stored = await events.list_after(run_id, 0)
        seqs = [event.seq for event in stored]
        assert seqs == list(range(1, len(seqs) + 1))

    assert len(seqs) == 90


@pytest.mark.asyncio
async def test_append_retries_on_seq_collision(repo):
    events, run_id = repo
    first = await events.append(
        session_id=uuid4(),
        run_id=run_id,
        event_type="heartbeat",
        actor="system",
        payload={"index": 0},
    )
    assert first.seq == 1

    real_next_seq = events.next_seq
    calls = 0

    async def conflicting_next_seq(run_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first.seq
        return await real_next_seq(run_id)

    events.next_seq = conflicting_next_seq
    second = await events.append(
        session_id=uuid4(),
        run_id=run_id,
        event_type="heartbeat",
        actor="system",
        payload={"index": 1},
    )

    assert second.seq == 2
    assert calls == 2
