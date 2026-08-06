from __future__ import annotations

import asyncio
from uuid import uuid4

from ai_dev_researcher.services.task_manager import TaskManager


async def test_request_cancel_returns_immediately_while_slow_running():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_executor(run_id):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    tm = TaskManager(lambda: slow_executor)
    run_id = uuid4()
    await tm.start_run(run_id)
    await asyncio.wait_for(started.wait(), timeout=1)

    # request_cancel must return quickly (not await the slow task).
    result = await asyncio.wait_for(tm.request_cancel(run_id), timeout=0.2)
    assert result is True
    await asyncio.wait_for(cancelled.wait(), timeout=1)


async def test_request_cancel_missing_task_returns_false():
    tm = TaskManager(lambda: None)
    assert await tm.request_cancel(uuid4()) is False
