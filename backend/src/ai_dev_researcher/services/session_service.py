from __future__ import annotations

from uuid import UUID

from ai_dev_researcher.core.errors import SessionNotFoundError
from ai_dev_researcher.domain.sessions import Session, make_slug
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.storage.paths import WorkspacePaths


class SessionService:
    def __init__(self, sessions: SessionRepository, paths: WorkspacePaths):
        self._sessions = sessions
        self._paths = paths

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
