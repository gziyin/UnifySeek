from __future__ import annotations

from datetime import datetime
from pathlib import Path
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

    async def rewrite_storage_paths(
        self, session_id: UUID, old_root: Path, new_root: Path
    ) -> int:
        artifacts = await self.list_for_session(session_id)
        old_root = old_root.resolve()
        new_root = new_root.resolve()
        updates: list[tuple[str | None, str | None, str]] = []
        for artifact in artifacts:
            original = self._rewrite_path(artifact.original_storage_path, old_root, new_root)
            normalized = self._rewrite_path(
                artifact.normalized_storage_path, old_root, new_root
            )
            if original != artifact.original_storage_path or normalized != artifact.normalized_storage_path:
                updates.append((original, normalized, str(artifact.artifact_id)))
        try:
            for original, normalized, artifact_id in updates:
                await self._conn.execute(
                    """
                    UPDATE artifacts
                    SET original_storage_path = ?, normalized_storage_path = ?
                    WHERE artifact_id = ?
                    """,
                    (original, normalized, artifact_id),
                )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return len(updates)

    @staticmethod
    def _rewrite_path(
        path: str | None, old_root: Path, new_root: Path
    ) -> str | None:
        if path is None:
            return None
        try:
            relative = Path(path).resolve().relative_to(old_root)
        except ValueError:
            return path
        return str(new_root / relative)

    async def delete(self, artifact_id: UUID) -> bool:
        """Delete the artifact DB record. Returns True if a row was removed."""
        cursor = await self._conn.execute(
            "DELETE FROM artifacts WHERE artifact_id = ?",
            (str(artifact_id),),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_by_session(self, session_id: UUID) -> int:
        """Delete all artifacts of a session. Returns the number of rows removed."""
        cursor = await self._conn.execute(
            "DELETE FROM artifacts WHERE session_id = ?",
            (str(session_id),),
        )
        await self._conn.commit()
        return cursor.rowcount
