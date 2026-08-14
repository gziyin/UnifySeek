from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

logger = logging.getLogger(__name__)

RunExecutor = Callable[[UUID], Awaitable[None]]

# on_stuck 回调：任务超过硬超时仍不结束（executor 内部预算被绕过）时，
# 用于把 run 收敛到终态（由组合根注入，含 runs repo + publisher 访问）。
OnStuck = Callable[[UUID], Awaitable[None]]


class TaskManager:
    def __init__(
        self,
        executor_factory: Callable[[], RunExecutor],
        *,
        on_stuck: OnStuck | None = None,
        shutdown_timeout: float = 15.0,
    ):
        self._executor_factory = executor_factory
        self._on_stuck = on_stuck
        self._shutdown_timeout = shutdown_timeout
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start_run(self, run_id: UUID, *, timeout: float = 0) -> None:
        """Schedule a run executor as a background task.

        ``timeout`` (>0) is the hard per-run deadline (last-resort watchdog):
        executor 内部预算（阶段/空闲/总时长）正常情况下先触发并自行收敛终态；
        若被绕过（executor 卡死在无法打断的路径），此处强制取消并触发
        ``on_stuck`` 回调收敛，保证 run 不永久 active、进程可退出（#40）。
        """
        async with self._lock:
            if run_id in self._tasks and not self._tasks[run_id].done():
                return
            executor = self._executor_factory()
            task = asyncio.create_task(
                self._guarded_run(run_id, executor, timeout),
                name=f"run:{run_id}",
            )
            self._tasks[run_id] = task
            task.add_done_callback(lambda finished: self._on_done(run_id, finished))

    async def has_live_task(self, run_id: UUID) -> bool:
        async with self._lock:
            task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def live_run_ids(self) -> list[UUID]:
        async with self._lock:
            return [run_id for run_id, task in self._tasks.items() if not task.done()]

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
        if not tasks:
            return
        gather = asyncio.gather(*tasks, return_exceptions=True)
        if self._shutdown_timeout > 0:
            try:
                await asyncio.wait_for(gather, timeout=self._shutdown_timeout)
            except asyncio.TimeoutError:
                # 兜底：任务无法在时限内收敛也不能阻塞进程退出（#40）。
                logger.warning(
                    "shutdown timed out after %.1fs waiting for %d run task(s); abandoning",
                    self._shutdown_timeout,
                    len(tasks),
                )
        else:
            await gather

    def _on_done(self, run_id: UUID, task: asyncio.Task[None]) -> None:
        try:
            exc = task.exception()
            if exc is not None:
                logger.exception("run task failed: %s", run_id, exc_info=exc)
        except asyncio.CancelledError:
            pass
        self._tasks.pop(run_id, None)

    async def _guarded_run(self, run_id: UUID, executor: RunExecutor, timeout: float) -> None:
        if timeout <= 0:
            await executor(run_id)
            return
        try:
            await asyncio.wait_for(executor(run_id), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                "run %s exceeded hard timeout %.1fs; forcing cancellation",
                run_id,
                timeout,
            )
            if self._on_stuck is not None:
                try:
                    result = self._on_stuck(run_id)
                    if inspect.isawaitable(result):
                        await result
                except Exception:  # noqa: BLE001 - 收敛失败只记录，不再抛
                    logger.exception("on_stuck convergence failed for run %s", run_id)
