from __future__ import annotations

import logging
from uuid import UUID

from ai_dev_researcher.domain.runs import RunStatus, TERMINAL_RUN_STATUSES
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.task_manager import TaskManager

logger = logging.getLogger(__name__)


async def converge_stuck_run(
    runs_repo: RunRepository,
    publisher: EventPublisher,
    run_id: UUID,
) -> None:
    """TaskManager 硬超时兜底：把仍未收敛的 run 置为终态（#40）。"""
    current = await runs_repo.get(run_id)
    if current is None or current.status in TERMINAL_RUN_STATUSES:
        return
    await runs_repo.update_status(
        run_id,
        RunStatus.INTERRUPTED,
        finished=True,
        error_code="RUN_TIMEOUT",
        error_message="Run killed by hard timeout",
    )
    await publisher.publish(
        session_id=current.session_id,
        run_id=run_id,
        event_type="run.failed",
        payload={
            "code": "RUN_TIMEOUT",
            "message": "Run killed by hard timeout",
            "retryable": False,
        },
    )
    logger.warning("converged run %s to interrupted (hard timeout)", run_id)


async def reap_stale_runs(
    runs_repo: RunRepository,
    publisher: EventPublisher,
    task_manager: TaskManager,
) -> int:
    """回收「task 已死但 run 仍 active」的行，避免 session 被 409 锁死（#40）。

    口径：task 仍存活则不回收；仅对无 live task 的 active run 收敛为 INTERRUPTED。
    """
    reclaimed = 0
    live = set(await task_manager.live_run_ids())
    for run in await runs_repo.list_active_runs():
        if run.run_id in live:
            continue  # task 仍存活则不回收
        try:
            await runs_repo.update_status(
                run.run_id,
                RunStatus.INTERRUPTED,
                finished=True,
                error_code="STALE_RECLAIMED",
                error_message="Run reclaimed: no live task and not converged",
            )
            await publisher.publish(
                session_id=run.session_id,
                run_id=run.run_id,
                event_type="run.failed",
                payload={
                    "code": "STALE_RECLAIMED",
                    "message": "Run reclaimed by stale-run reaper",
                    "retryable": False,
                },
            )
            reclaimed += 1
        except Exception:  # noqa: BLE001 - 单条回收失败不阻断其余
            logger.exception("failed to reclaim stale run %s", run.run_id)
    if reclaimed:
        logger.warning("reclaimed %d stale run(s)", reclaimed)
    return reclaimed
