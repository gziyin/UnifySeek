from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from uuid import UUID

import aiosqlite

from ai_dev_researcher.domain.events import EventType, ResearchEvent


class EventRepository:
    _MAX_SEQ_RETRIES = 5

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._seq_lock = asyncio.Lock()

    async def next_seq(self, run_id: UUID) -> int:
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM events WHERE run_id = ?",
            (str(run_id),),
        )
        row = await cursor.fetchone()
        return int(row["max_seq"]) + 1

    async def high_seq(self, run_id: UUID) -> int:
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM events WHERE run_id = ?",
            (str(run_id),),
        )
        row = await cursor.fetchone()
        return int(row["max_seq"])

    async def append(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        event_type: EventType,
        actor: str,
        payload: dict,
    ) -> ResearchEvent:
        # Serialize seq computation + insert within one process, and retry on
        # UNIQUE(run_id, seq) collisions as a defensive path for other writers.
        async with self._seq_lock:
            for attempt in range(self._MAX_SEQ_RETRIES):
                seq = await self.next_seq(run_id)
                event = ResearchEvent(
                    seq=seq,
                    session_id=session_id,
                    run_id=run_id,
                    type=event_type,
                    actor=actor,
                    payload=payload,
                )
                try:
                    await self._conn.execute(
                        """
                        INSERT INTO events (
                            event_id, run_id, session_id, seq, type, occurred_at,
                            actor, protocol_version, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(event.event_id),
                            str(event.run_id),
                            str(event.session_id),
                            event.seq,
                            event.type,
                            event.occurred_at.isoformat(),
                            event.actor,
                            event.protocol_version,
                            json.dumps(event.payload, ensure_ascii=False),
                        ),
                    )
                    await self._conn.commit()
                    return event
                except sqlite3.IntegrityError:
                    await self._conn.rollback()
                    if attempt == self._MAX_SEQ_RETRIES - 1:
                        raise

    def _row_to_event(self, row: aiosqlite.Row) -> ResearchEvent:
        return ResearchEvent(
            event_id=UUID(row["event_id"]),
            seq=row["seq"],
            session_id=UUID(row["session_id"]),
            run_id=UUID(row["run_id"]),
            type=row["type"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            actor=row["actor"],
            protocol_version=row["protocol_version"],
            payload=json.loads(row["payload_json"]),
        )

    async def delete_by_session(self, session_id: UUID) -> int:
        """Delete all events of a session. Returns the number of rows removed."""
        cursor = await self._conn.execute(
            "DELETE FROM events WHERE session_id = ?",
            (str(session_id),),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def list_after(self, run_id: UUID, after_seq: int) -> list[ResearchEvent]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM events
            WHERE run_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (str(run_id), after_seq),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def list_range(
        self,
        run_id: UUID,
        after_seq: int,
        high_seq: int,
    ) -> list[ResearchEvent]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM events
            WHERE run_id = ? AND seq > ? AND seq <= ?
            ORDER BY seq ASC
            """,
            (str(run_id), after_seq, high_seq),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]
