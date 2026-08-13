from __future__ import annotations

import logging
import shutil
from uuid import UUID

from ai_dev_researcher.core.errors import SessionNotFoundError
from ai_dev_researcher.domain.sessions import Session, make_slug
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.storage.paths import WorkspacePaths

logger = logging.getLogger(__name__)


class SessionService:
    def __init__(
        self,
        sessions: SessionRepository,
        paths: WorkspacePaths,
        *,
        runs: RunRepository | None = None,
        artifacts: ArtifactRepository | None = None,
        events: EventRepository | None = None,
        evidence: EvidenceRepository | None = None,
    ):
        self._sessions = sessions
        self._paths = paths
        self._runs = runs
        self._artifacts = artifacts
        self._events = events
        self._evidence = evidence

    async def create_session(self, display_name: str | None = None) -> Session:
        """Create a session and persist it.

        The on-disk layout is created lazily on first upload/run so a brand-new
        session can adopt slug-style directory naming (``slug-8位短uuid``) at
        its first research run. Passing an explicit ``display_name`` here also
        works for API consumers that know the question up front; the stored
        value is the slug form (首次研究问题 → slug 工具生成).
        """
        stored_name = make_slug(display_name) if display_name else None
        session = Session(display_name=stored_name)
        session = await self._sessions.create(session)
        if stored_name:
            self._paths.ensure_session_layout(session.session_id, display_name=stored_name)
        return session

    async def get_session(self, session_id: UUID) -> Session:
        session = await self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"session not found: {session_id}")
        return session

    async def list_sessions(self) -> list[Session]:
        return await self._sessions.list()

    async def delete_session(self, session_id: UUID) -> bool:
        """Delete an entire session: dependent rows, on-disk directory, then the row.

        Returns False if the session does not exist. Deletion order matters:
        ``PRAGMA foreign_keys = ON`` with no ``ON DELETE CASCADE`` on
        ``runs.session_id`` / ``artifacts.session_id`` means child rows must be
        removed before the ``sessions`` row, or SQLite raises an FK violation.
        The on-disk directory removal is fail-soft (never blocks the delete).
        """
        if await self._sessions.get(session_id) is None:
            return False

        run_ids = await self._runs.run_ids_for_session(session_id) if self._runs else []
        if run_ids and self._evidence:
            await self._evidence.delete_by_run_ids(run_ids)
        if self._runs:
            await self._runs.delete_by_session(session_id)
        if self._artifacts:
            await self._artifacts.delete_by_session(session_id)
        if self._events:
            await self._events.delete_by_session(session_id)

        try:
            session_dir = self._paths.session_dir(session_id)
            if session_dir.exists() and session_dir.is_dir():
                shutil.rmtree(session_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001 - fail-soft: removal failure never blocks
            logger.warning("failed to remove session dir for %s", session_id, exc_info=True)

        return await self._sessions.delete(session_id)

    async def set_display_name(self, session_id: UUID, display_name: str) -> Session:
        """Set the session's display name from the first research question.

        Also ensures the slug-style session layout so the directory is created
        under ``<slug>-<8位短uuid>`` on first run. Existing directories are
        never renamed (backward compatible: 存量 UUID 目录继续可访问).
        """
        session = await self.get_session(session_id)
        if session.display_name == display_name:
            return session
        slug = make_slug(display_name)
        updated = await self._sessions.update_display_name(session_id, slug)
        if updated is None:
            raise SessionNotFoundError(f"session not found: {session_id}")
        self._paths.ensure_session_layout(session_id, display_name=slug)
        return updated
