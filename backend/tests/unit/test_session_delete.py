from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus
from ai_dev_researcher.domain.sessions import Session
from ai_dev_researcher.main import create_app
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.session_service import SessionService
from ai_dev_researcher.storage.paths import WorkspacePaths


async def _build_service(tmp_path: Path) -> tuple[SessionService, dict]:
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    paths = WorkspacePaths(tmp_path / "sessions")
    sessions = SessionRepository(conn)
    runs = RunRepository(conn)
    artifacts = ArtifactRepository(conn)
    events = EventRepository(conn)
    evidence = EvidenceRepository(conn)
    service = SessionService(
        sessions,
        paths,
        runs=runs,
        artifacts=artifacts,
        events=events,
        evidence=evidence,
    )
    repos = {
        "conn": conn,
        "sessions": sessions,
        "runs": runs,
        "artifacts": artifacts,
        "events": events,
        "evidence": evidence,
        "paths": paths,
    }
    return service, repos


async def _seed_session(repos: dict) -> tuple[Session, Run]:
    """Create a session with one run, one artifact, one event and one evidence row."""
    conn = repos["conn"]
    session = await repos["sessions"].create(Session(display_name="delete-me"))
    run = await repos["runs"].create(
        Run(
            session_id=session.session_id,
            status=RunStatus.SUCCEEDED,
            request=ResearchRequest(question="q"),
            created_at=datetime.now(timezone.utc),
        )
    )
    await repos["artifacts"].create(
        Artifact(
            session_id=session.session_id,
            run_id=run.run_id,
            kind=ArtifactKind.REPORT,
            display_name="report.md",
            mime_type="text/markdown",
        )
    )
    await repos["events"].append(
        session_id=session.session_id,
        run_id=run.run_id,
        event_type="run.succeeded",
        actor="system",
        payload={"ok": True},
    )
    await repos["evidence"].create(
        EvidenceRecord(
            id="S1",
            run_id=run.run_id,
            source_type="web",
            evidence_level="search_snippet",
            title="source",
            locator="https://example.com",
            canonical_url="https://example.com",
            excerpt="excerpt",
        )
    )
    # 会话目录（含 run 子目录），验证删除时目录被移除。
    repos["paths"].ensure_run_layout(session.session_id, run.run_id, display_name="delete-me")
    return session, run


@pytest.mark.asyncio
async def test_delete_session_removes_rows_and_directory(tmp_path: Path):
    service, repos = await _build_service(tmp_path)
    session, run = await _seed_session(repos)
    session_id = session.session_id

    assert await service.delete_session(session_id) is True

    assert await repos["sessions"].get(session_id) is None
    assert await repos["runs"].get(run.run_id) is None
    assert await repos["artifacts"].list_for_session(session_id) == []
    assert await repos["events"].list_after(run.run_id, 0) == []
    assert await repos["evidence"].list_for_run(run.run_id) == []

    # 目录被移除
    session_dir = repos["paths"].session_dir(session_id, display_name="delete-me")
    assert not session_dir.exists()
    await repos["conn"].close()


@pytest.mark.asyncio
async def test_delete_session_removes_evidence_via_run_ids(tmp_path: Path):
    """evidence 无 FK、无级联，必须按 run_id 批量删除（Leader 裁决 1）。"""
    service, repos = await _build_service(tmp_path)
    session, run = await _seed_session(repos)

    await service.delete_session(session.session_id)
    assert await repos["evidence"].list_for_run(run.run_id) == []
    await repos["conn"].close()


@pytest.mark.asyncio
async def test_delete_session_nonexistent_returns_false(tmp_path: Path):
    service, repos = await _build_service(tmp_path)
    from uuid import uuid4

    assert await service.delete_session(uuid4()) is False
    await repos["conn"].close()


# ---------------------------------------------------------------------------
# API 层：DELETE /api/sessions/{id}
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        fake_agent_mode=True,
        cors_origins="http://127.0.0.1:5173",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_api_delete_session_204_then_get_404(client: AsyncClient):
    created = await client.post("/api/sessions")
    session_id = created.json()["session_id"]

    resp = await client.delete(f"/api/sessions/{session_id}")
    assert resp.status_code == 204

    # 删除后再 GET 返回 404
    get_resp = await client.get(f"/api/sessions/{session_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_api_delete_session_nonexistent_404(client: AsyncClient):
    resp = await client.delete("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
