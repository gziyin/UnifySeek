from __future__ import annotations

from datetime import datetime
from uuid import UUID

import aiosqlite

from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus


class ArtifactRepository:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def create(self, artifact: Artifact) -> Artifact:
        await self._conn.execute(
            """
            INSERT INTO artifacts (
                artifact_id, session_id, run_id, kind, display_name, mime_type,
                size_bytes, parse_status, original_storage_path,
                normalized_storage_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(artifact.artifact_id),
                str(artifact.session_id),
                str(artifact.run_id) if artifact.run_id else None,
                artifact.kind.value,
                artifact.display_name,
                artifact.mime_type,
                artifact.size_bytes,
                artifact.parse_status.value,
                artifact.original_storage_path,
                artifact.normalized_storage_path,
                artifact.created_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return artifact

    def _row_to_artifact(self, row: aiosqlite.Row) -> Artifact:
        return Artifact(
            artifact_id=UUID(row["artifact_id"]),
            session_id=UUID(row["session_id"]),
            run_id=UUID(row["run_id"]) if row["run_id"] else None,
            kind=ArtifactKind(row["kind"]),
            display_name=row["display_name"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            parse_status=ParseStatus(row["parse_status"]),
            original_storage_path=row["original_storage_path"],
            normalized_storage_path=row["normalized_storage_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    async def get(self, artifact_id: UUID) -> Artifact | None:
        cursor = await self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?",
            (str(artifact_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_artifact(row)

    async def list_for_session(self, session_id: UUID) -> list[Artifact]:
        cursor = await self._conn.execute(
            "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at ASC",
            (str(session_id),),
        )
        rows = await cursor.fetchall()
        return [self._row_to_artifact(row) for row in rows]

    async def get_many(self, artifact_ids: list[UUID]) -> list[Artifact]:
        if not artifact_ids:
            return []
        placeholders = ",".join("?" for _ in artifact_ids)
        cursor = await self._conn.execute(
            f"SELECT * FROM artifacts WHERE artifact_id IN ({placeholders})",
            [str(item) for item in artifact_ids],
        )
        rows = await cursor.fetchall()
        return [self._row_to_artifact(row) for row in rows]
