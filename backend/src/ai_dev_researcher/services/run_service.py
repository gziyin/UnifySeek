from __future__ import annotations

from uuid import UUID

from ai_dev_researcher.core.errors import (
    RunConflictError,
    RunNotFoundError,
    SessionNotFoundError,
)
from ai_dev_researcher.domain.artifacts import ArtifactKind
from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus, TERMINAL_RUN_STATUSES
from ai_dev_researcher.domain.sessions import utc_now
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.storage.paths import WorkspacePaths


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
    ):
        self._sessions = sessions
        self._runs = runs
        self._artifacts = artifacts
        self._paths = paths
        self._publisher = publisher
        self._task_manager = task_manager

    async def create_run(self, session_id: UUID, request: ResearchRequest) -> Run:
        session = await self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"session not found: {session_id}")

        active = await self._runs.find_active_for_session(session_id)
        if active is not None:
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
        self._paths.ensure_run_layout(session_id, run.run_id)
        await self._sessions.touch(session_id)
        await self._task_manager.start_run(run.run_id)
        return run

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
        await self._task_manager.cancel_run(run_id)
        return updated
