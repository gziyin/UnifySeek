from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

logger = logging.getLogger(__name__)

RunExecutor = Callable[[UUID], Awaitable[None]]


class TaskManager:
    def __init__(self, executor_factory: Callable[[], RunExecutor]):
        self._executor_factory = executor_factory
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start_run(self, run_id: UUID) -> None:
        async with self._lock:
            if run_id in self._tasks and not self._tasks[run_id].done():
                return
            executor = self._executor_factory()
            task = asyncio.create_task(self._guarded_run(run_id, executor), name=f"run:{run_id}")
            self._tasks[run_id] = task
            task.add_done_callback(lambda finished: self._on_done(run_id, finished))

    async def request_cancel(self, run_id: UUID) -> bool:
        """Request cancellation without waiting for the task to finish.

        Returns True if a live task was found and ``cancel()`` was requested;
        False if there is no such task (missing or already completed). The
        terminal transition to ``cancelled`` is converged by the executor's
        CancelledError handling or by the caller (RunService).
        """
        async with self._lock:
            task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _on_done(self, run_id: UUID, task: asyncio.Task[None]) -> None:
        try:
            exc = task.exception()
            if exc is not None:
                logger.exception("run task failed: %s", run_id, exc_info=exc)
        except asyncio.CancelledError:
            pass
        self._tasks.pop(run_id, None)

    async def _guarded_run(self, run_id: UUID, executor: RunExecutor) -> None:
        await executor(run_id)
