from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus
from ai_dev_researcher.domain.sessions import Session
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.upload_service import UploadService
from ai_dev_researcher.storage.paths import WorkspacePaths


async def _build(
    tmp_path: Path,
) -> tuple[UploadService, aiosqlite.Connection, SessionRepository, ArtifactRepository]:
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    paths = WorkspacePaths(tmp_path / "sessions")
    settings = Settings()
    sessions = SessionRepository(conn)
    artifacts = ArtifactRepository(conn)
    service = UploadService(
        sessions=sessions,
        artifacts=artifacts,
        paths=paths,
        settings=settings,
        vector_store=None,
    )
    return service, conn, sessions, artifacts


def test_max_upload_bytes_defaults_to_50mb():
    """issue #28：单文件大小上限默认 50 MiB。"""
    assert Settings().max_upload_bytes == 50 * 1024 * 1024


@pytest.mark.asyncio
async def test_delete_upload_removes_artifact(tmp_path: Path):
    service, conn, sessions, artifacts = await _build(tmp_path)
    session = await sessions.create(Session())
    up = await service.upload(
        session_id=session.session_id,
        filename="a.md",
        content_type="text/markdown",
        data="# hello world\n正文内容".encode("utf-8"),
    )
    assert up.kind == ArtifactKind.UPLOAD

    deleted = await service.delete_artifact(
        session_id=session.session_id,
        artifact_id=up.artifact_id,
    )
    assert deleted is True
    assert await artifacts.get(up.artifact_id) is None
    await conn.close()


@pytest.mark.asyncio
async def test_delete_upload_requires_same_session(tmp_path: Path):
    service, conn, sessions, artifacts = await _build(tmp_path)
    session_a = await sessions.create(Session())
    session_b = await sessions.create(Session())
    up = await service.upload(
        session_id=session_a.session_id,
        filename="a.md",
        content_type="text/markdown",
        data=b"# hello",
    )

    deleted = await service.delete_artifact(
        session_id=session_b.session_id,
        artifact_id=up.artifact_id,
    )
    assert deleted is False
    assert await artifacts.get(up.artifact_id) is not None
    await conn.close()


@pytest.mark.asyncio
async def test_delete_ignores_non_upload_artifact(tmp_path: Path):
    service, conn, sessions, artifacts = await _build(tmp_path)
    session = await sessions.create(Session())
    report = Artifact(
        session_id=session.session_id,
        kind=ArtifactKind.REPORT,
        display_name="r.md",
        mime_type="text/markdown",
        size_bytes=1,
        parse_status=ParseStatus.SKIPPED,
    )
    created = await artifacts.create(report)

    deleted = await service.delete_artifact(
        session_id=session.session_id,
        artifact_id=created.artifact_id,
    )
    assert deleted is False
    assert await artifacts.get(created.artifact_id) is not None
    await conn.close()


@pytest.mark.asyncio
async def test_delete_unknown_artifact_returns_false(tmp_path: Path):
    service, conn, sessions, artifacts = await _build(tmp_path)
    session = await sessions.create(Session())
    from uuid import uuid4

    deleted = await service.delete_artifact(
        session_id=session.session_id,
        artifact_id=uuid4(),
    )
    assert deleted is False
    await conn.close()
