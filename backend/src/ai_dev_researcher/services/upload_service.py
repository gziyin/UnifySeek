from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.core.errors import (
    DocumentParseError,
    InvalidUploadError,
    SessionNotFoundError,
)
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.storage.normalized_docs import (
    guess_mime,
    normalize_document,
    sanitize_display_name,
)
from ai_dev_researcher.storage.paths import WorkspacePaths


class UploadService:
    def __init__(
        self,
        *,
        sessions: SessionRepository,
        artifacts: ArtifactRepository,
        paths: WorkspacePaths,
        settings: Settings,
    ):
        self._sessions = sessions
        self._artifacts = artifacts
        self._paths = paths
        self._settings = settings

    async def upload(
        self,
        *,
        session_id: UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> Artifact:
        session = await self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"session not found: {session_id}")

        existing = await self._artifacts.list_for_session(session_id)
        uploads = [item for item in existing if item.kind == ArtifactKind.UPLOAD]
        if len(uploads) >= self._settings.max_uploads_per_session:
            raise InvalidUploadError("session already has maximum uploads")

        if len(data) > self._settings.max_upload_bytes:
            raise InvalidUploadError("file exceeds 10 MiB limit")

        display_name = sanitize_display_name(filename)
        mime = guess_mime(display_name) or content_type
        if mime is None or Path(display_name).suffix.lower() not in {
            ".pdf",
            ".docx",
            ".md",
            ".txt",
        }:
            raise InvalidUploadError("unsupported file type; allow pdf/docx/md/txt")

        self._paths.ensure_session_layout(session_id)
        artifact = Artifact(
            session_id=session_id,
            kind=ArtifactKind.UPLOAD,
            display_name=display_name,
            mime_type=mime,
            size_bytes=len(data),
            parse_status=ParseStatus.PENDING,
        )
        original_path = self._paths.upload_path(session_id, artifact.artifact_id)
        # Keep original extension on a sibling typed copy for parsers.
        typed_path = original_path.with_suffix(Path(display_name).suffix.lower())
        original_path.write_bytes(data)
        shutil.copyfile(original_path, typed_path)

        normalized_path = self._paths.normalized_path(session_id, artifact.artifact_id)
        artifact.original_storage_path = str(original_path)
        parse_error: Exception | None = None
        try:
            text = normalize_document(
                typed_path,
                max_chars=self._settings.max_normalized_chars,
            )
            normalized_path.write_text(text, encoding="utf-8")
            artifact.parse_status = ParseStatus.PARSED
            artifact.normalized_storage_path = str(normalized_path)
        except Exception as exc:  # noqa: BLE001 - record failed artifact then raise
            artifact.parse_status = ParseStatus.FAILED
            parse_error = exc
        finally:
            if typed_path.exists() and typed_path != original_path:
                typed_path.unlink(missing_ok=True)

        await self._artifacts.create(artifact)
        await self._sessions.touch(session_id)
        if parse_error is not None:
            raise DocumentParseError(f"failed to parse upload: {parse_error}") from parse_error
        return artifact
