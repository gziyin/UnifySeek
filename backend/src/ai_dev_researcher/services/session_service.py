from __future__ import annotations

from uuid import UUID

from ai_dev_researcher.core.errors import SessionNotFoundError
from ai_dev_researcher.domain.sessions import Session
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.storage.paths import WorkspacePaths


class SessionService:
    def __init__(self, sessions: SessionRepository, paths: WorkspacePaths):
        self._sessions = sessions
        self._paths = paths

    async def create_session(self) -> Session:
        session = await self._sessions.create()
        self._paths.ensure_session_layout(session.session_id)
        return session

    async def get_session(self, session_id: UUID) -> Session:
        session = await self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"session not found: {session_id}")
        return session
