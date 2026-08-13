from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import aiosqlite

from ai_dev_researcher.domain.runs import (
    ACTIVE_RUN_STATUSES,
    ResearchRequest,
    Run,
    RunStatus,
)
from ai_dev_researcher.domain.sessions import utc_now


class RunRepository:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def create(self, run: Run) -> Run:
        await self._conn.execute(
            """
            INSERT INTO runs (
                run_id, session_id, status, request_json, created_at,
                started_at, finished_at, cancel_requested_at,
                error_code, error_message, report_artifact_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.run_id),
                str(run.session_id),
                run.status.value,
                json.dumps(run.request.model_dump(mode="json"), ensure_ascii=False),
                run.created_at.isoformat(),
                run.started_at.isoformat() if run.started_at else None,
                run.finished_at.isoformat() if run.finished_at else None,
                run.cancel_requested_at.isoformat() if run.cancel_requested_at else None,
                run.error_code,
                run.error_message,
                str(run.report_artifact_id) if run.report_artifact_id else None,
            ),
        )
        await self._conn.commit()
        return run

    def _row_to_run(self, row: aiosqlite.Row) -> Run:
        return Run(
            run_id=UUID(row["run_id"]),
            session_id=UUID(row["session_id"]),
            status=RunStatus(row["status"]),
            request=ResearchRequest.model_validate(json.loads(row["request_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            cancel_requested_at=(
                datetime.fromisoformat(row["cancel_requested_at"])
                if row["cancel_requested_at"]
                else None
            ),
            error_code=row["error_code"],
            error_message=row["error_message"],
            report_artifact_id=(
                UUID(row["report_artifact_id"]) if row["report_artifact_id"] else None
            ),
        )

    async def get(self, run_id: UUID) -> Run | None:
        cursor = await self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (str(run_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    async def find_active_for_session(self, session_id: UUID) -> Run | None:
        placeholders = ",".join("?" for _ in ACTIVE_RUN_STATUSES)
        cursor = await self._conn.execute(
            f"""
            SELECT * FROM runs
            WHERE session_id = ? AND status IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(session_id), *[s.value for s in ACTIVE_RUN_STATUSES]),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    async def list_for_session(
        self, session_id: UUID, limit: int = 50, offset: int = 0
    ) -> list[Run]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM runs
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (str(session_id), limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_run(row) for row in rows]

    async def update_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        report_artifact_id: UUID | None = None,
        started: bool = False,
        finished: bool = False,
        cancel_requested: bool = False,
    ) -> Run:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        if run.status != status:
            run.assert_can_transition(status)
        now = utc_now()
        started_at = run.started_at or (now if started else None)
        finished_at = now if finished else run.finished_at
        cancel_at = now if cancel_requested else run.cancel_requested_at
        report_id = report_artifact_id or run.report_artifact_id
        await self._conn.execute(
            """
            UPDATE runs SET
                status = ?,
                started_at = ?,
                finished_at = ?,
                cancel_requested_at = ?,
                error_code = ?,
                error_message = ?,
                report_artifact_id = ?
            WHERE run_id = ?
            """,
            (
                status.value,
                started_at.isoformat() if started_at else None,
                finished_at.isoformat() if finished_at else None,
                cancel_at.isoformat() if cancel_at else None,
                error_code if error_code is not None else run.error_code,
                error_message if error_message is not None else run.error_message,
                str(report_id) if report_id else None,
                str(run_id),
            ),
        )
        await self._conn.commit()
        updated = await self.get(run_id)
        assert updated is not None
        return updated

    async def run_ids_for_session(self, session_id: UUID) -> list[UUID]:
        """All run ids for a session (used to clean per-run rows like evidence)."""
        cursor = await self._conn.execute(
            "SELECT run_id FROM runs WHERE session_id = ?",
            (str(session_id),),
        )
        rows = await cursor.fetchall()
        return [UUID(row["run_id"]) for row in rows]

    async def delete_by_session(self, session_id: UUID) -> int:
        """Delete all runs of a session. Returns the number of rows removed."""
        cursor = await self._conn.execute(
            "DELETE FROM runs WHERE session_id = ?",
            (str(session_id),),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def mark_stale_interrupted(self) -> int:
        now = utc_now().isoformat()
        placeholders = ",".join("?" for _ in ACTIVE_RUN_STATUSES)
        cursor = await self._conn.execute(
            f"""
            UPDATE runs
            SET status = ?, finished_at = ?, error_code = ?, error_message = ?
            WHERE status IN ({placeholders})
            """,
            (
                RunStatus.INTERRUPTED.value,
                now,
                "SERVER_RESTART",
                "Run interrupted by server restart",
                *[s.value for s in ACTIVE_RUN_STATUSES],
            ),
        )
        await self._conn.commit()
        return cursor.rowcount
