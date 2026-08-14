from __future__ import annotations

import logging
from uuid import UUID

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.core.errors import (
    RunConflictError,
    RunNotFoundError,
    SessionNotFoundError,
)
from ai_dev_researcher.domain.artifacts import ArtifactKind
from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus, TERMINAL_RUN_STATUSES
from ai_dev_researcher.domain.sessions import make_slug, utc_now
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.storage.paths import WorkspacePaths

logger = logging.getLogger(__name__)


class RunService:
    def __init__(
        self,
        *,
        sessions: SessionRepository,
        runs: RunRepository,
        artifacts: ArtifactRepository,
        paths: WorkspacePaths,
        publisher: EventPublisher,
        task_manager: TaskManager,
        settings: Settings | None = None,
    ):
        self._sessions = sessions
        self._runs = runs
        self._artifacts = artifacts
        self._paths = paths
        self._publisher = publisher
        self._task_manager = task_manager
        self._settings = settings

    async def create_run(self, session_id: UUID, request: ResearchRequest) -> Run:
        session = await self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"session not found: {session_id}")

        active = await self._runs.find_active_for_session(session_id)
        if active is not None:
            if active.status == RunStatus.CANCELLING:
                # 上一次 run 处于取消中但尚未收敛到终态（异步取消的竞态窗口）。
                # 用户主动取消后想立即开始新研究：先兜底取消其后台任务，再把该 run
                # 收敛为 cancelled（cancelling→cancelled 在 ALLOWED_TRANSITIONS 中合法），
                # 然后允许创建新 run。其余 active 状态（RUNNING/PENDING）仍拒绝并发覆盖。
                await self._task_manager.request_cancel(active.run_id)
                current = await self._runs.get(active.run_id)
                if current and current.status not in TERMINAL_RUN_STATUSES:
                    await self._runs.update_status(
                        active.run_id,
                        RunStatus.CANCELLED,
                        finished=True,
                        cancel_requested=True,
                        error_code="CANCELLED",
                        error_message="Cancelled (superseded by new run)",
                    )
                    await self._publisher.publish(
                        session_id=session_id,
                        run_id=active.run_id,
                        event_type="run.cancelled",
                        payload={"reason": "cancelled_superseded_by_new_run"},
                    )
            else:
                raise RunConflictError("session already has an active run")

        if request.uploaded_artifact_ids:
            found = await self._artifacts.get_many(request.uploaded_artifact_ids)
            found_ids = {item.artifact_id for item in found}
            missing = [str(item) for item in request.uploaded_artifact_ids if item not in found_ids]
            if missing:
                raise SessionNotFoundError(f"artifact not in session: {', '.join(missing)}")
            for item in found:
                if item.session_id != session_id or item.kind != ArtifactKind.UPLOAD:
                    raise SessionNotFoundError(f"artifact not authorized: {item.artifact_id}")

        run = Run(session_id=session_id, request=request, status=RunStatus.PENDING)
        await self._runs.create(run)
        display_name = await self._resolve_display_name(session, request)
        self._paths.ensure_run_layout(session_id, run.run_id, display_name=display_name)
        await self._sessions.touch(session_id)
        await self._task_manager.start_run(run.run_id, timeout=self._hard_run_timeout(run))
        return run

    def _hard_run_timeout(self, run: Run) -> float:
        """TaskManager 硬超时 = run 总预算（含 constraints 覆盖）+ grace。

        总预算为 0（不限制）时返回 0 表示禁用硬超时，与 executor 内总预算语义一致。
        """
        if self._settings is None:
            return 0.0
        max_elapsed = self._settings.agent_max_elapsed_seconds
        for constraint in run.request.constraints:
            stripped = constraint.strip()
            for sep in ("=", ":"):
                if sep not in stripped:
                    continue
                key, value = (part.strip() for part in stripped.split(sep, 1))
                if key == "max_elapsed_seconds":
                    try:
                        max_elapsed = max(0.0, float(value))
                    except ValueError:
                        pass
        if not max_elapsed:
            return 0.0
        return max_elapsed + self._settings.agent_hard_timeout_grace_seconds

    async def _resolve_display_name(self, session, request: ResearchRequest) -> str | None:
        """Resolve the display name used to name the session directory.

        First run (session has no display name yet): derive the slug from the
        research question and persist it so the directory is created as
        ``<slug>-<8位短uuid>``. Naming failure must never block run creation,
        so on error we fall back to ``None`` (legacy UUID directory) and log.

        Later runs keep the already-persisted display name (sticky).
        """
        if session.display_name:
            return session.display_name
        if not request.question:
            return None
        slug = make_slug(request.question)
        try:
            updated = await self._sessions.update_display_name(session.session_id, slug)
            if updated is not None:
                return updated.display_name
        except Exception:  # noqa: BLE001 - naming must not block run creation
            logger.exception("failed to set session display_name for %s", session.session_id)
        return None

    async def get_run(self, run_id: UUID) -> Run:
        run = await self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return run

    async def cancel_run(self, run_id: UUID) -> Run:
        run = await self.get_run(run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        if run.status == RunStatus.CANCELLING:
            return run
        if run.status == RunStatus.PENDING:
            updated = await self._runs.update_status(
                run_id,
                RunStatus.CANCELLED,
                finished=True,
                cancel_requested=True,
                error_code="CANCELLED",
                error_message="Cancelled before start",
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.cancelled",
                payload={"reason": "cancelled_before_start"},
            )
            return updated

        updated = await self._runs.update_status(
            run_id,
            RunStatus.CANCELLING,
            cancel_requested=True,
        )
        await self._publisher.publish(
            session_id=run.session_id,
            run_id=run_id,
            event_type="run.cancelling",
            payload={"requested_at": utc_now().isoformat()},
        )
        cancelled_requested = await self._task_manager.request_cancel(run_id)
        if not cancelled_requested:
            # No live task to cancel (missing or already completed): re-read the
            # run so we never override a concurrent terminal success, then converge
            # to cancelled so the run never stays stuck in CANCELLING.
            current = await self._runs.get(run_id)
            if current and current.status in TERMINAL_RUN_STATUSES:
                return current
            final = await self._runs.update_status(
                run_id,
                RunStatus.CANCELLED,
                finished=True,
                cancel_requested=True,
                error_code="CANCELLED",
                error_message="Cancelled (no active task)",
            )
            await self._publisher.publish(
                session_id=run.session_id,
                run_id=run_id,
                event_type="run.cancelled",
                payload={"reason": "cancelled_no_active_task"},
            )
            return final
        return updated
