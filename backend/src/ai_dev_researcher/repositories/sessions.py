from __future__ import annotations

from datetime import datetime
from uuid import UUID

import aiosqlite

from ai_dev_researcher.domain.sessions import Session, utc_now


class SessionRepository:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def create(self, session: Session | None = None) -> Session:
        item = session or Session()
        await self._conn.execute(
            """
            INSERT INTO sessions (session_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(item.session_id),
                item.status,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return item

    async def get(self, session_id: UUID) -> Session | None:
        cursor = await self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (str(session_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Session(
            session_id=UUID(row["session_id"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def touch(self, session_id: UUID) -> None:
        await self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (utc_now().isoformat(), str(session_id)),
        )
        await self._conn.commit()
