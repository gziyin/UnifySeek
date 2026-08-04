from __future__ import annotations

from datetime import datetime
from uuid import UUID

import aiosqlite

from ai_dev_researcher.domain.sessions import Session, utc_now


class SessionRepository:
    """SQLite repository for sessions.

    Persists the new ``display_name`` column. Because the base schema in
    ``repositories/sqlite.py`` predates this field, the repository performs a
    lazy, idempotent ``ALTER TABLE ... ADD COLUMN`` migration on first use so
    that both fresh and pre-existing databases stay compatible.
    """

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._display_name_checked = False

    async def _ensure_display_name_column(self) -> None:
        if self._display_name_checked:
            return
        cursor = await self._conn.execute("PRAGMA table_info(sessions)")
        rows = await cursor.fetchall()
        names = {row["name"] for row in rows}
        if "display_name" not in names:
            await self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN display_name TEXT"
            )
            await self._conn.commit()
        self._display_name_checked = True

    async def create(self, session: Session | None = None) -> Session:
        await self._ensure_display_name_column()
        item = session or Session()
        await self._conn.execute(
            """
            INSERT INTO sessions (session_id, display_name, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(item.session_id),
                item.display_name,
                item.status,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return item

    async def get(self, session_id: UUID) -> Session | None:
        await self._ensure_display_name_column()
        cursor = await self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (str(session_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    async def list(self) -> list[Session]:
        await self._ensure_display_name_column()
        cursor = await self._conn.execute(
            "SELECT * FROM sessions ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_session(row) for row in rows]

    async def update_display_name(
        self, session_id: UUID, display_name: str
    ) -> Session | None:
        await self._ensure_display_name_column()
        await self._conn.execute(
            "UPDATE sessions SET display_name = ?, updated_at = ? WHERE session_id = ?",
            (display_name, utc_now().isoformat(), str(session_id)),
        )
        await self._conn.commit()
        return await self.get(session_id)

    async def touch(self, session_id: UUID) -> None:
        await self._ensure_display_name_column()
        await self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (utc_now().isoformat(), str(session_id)),
        )
        await self._conn.commit()

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> Session:
        return Session(
            session_id=UUID(row["session_id"]),
            display_name=row["display_name"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
