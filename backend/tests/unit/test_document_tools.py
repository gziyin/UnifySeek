from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.document_reader import record_document_evidence_impl


@pytest.fixture
async def env(tmp_path: Path):
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    session = await SessionRepository(conn).create()
    run_id = uuid4()
    paths = WorkspacePaths(tmp_path / "workspace")
    paths.ensure_run_layout(session.session_id, run_id)
    artifact = Artifact(
        session_id=session.session_id,
        run_id=None,
        kind=ArtifactKind.UPLOAD,
        display_name="上传材料.pdf",
        mime_type="application/pdf",
        parse_status=ParseStatus.PARSED,
        normalized_storage_path=str(tmp_path / "normalized.txt"),
    )
    await ArtifactRepository(conn).create(artifact)
    store = EvidenceStore(
        run_id=run_id,
        session_id=session.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    context = RunContext(
        run_id=run_id,
        session_id=session.session_id,
        question="q",
        uploaded_artifact_ids=[artifact.artifact_id],
        max_web_sources=3,
        paths=paths,
        settings=type("Settings", (), {})(),
    )
    try:
        yield context, store, ArtifactRepository(conn), artifact
    finally:
        await conn.close()


async def test_record_document_evidence_persists_artifact_id(env):
    context, store, artifacts, artifact = env
    result = await record_document_evidence_impl(
        context=context,
        store=store,
        artifacts=artifacts,
        artifact_id=str(artifact.artifact_id),
        title="证据标题",
        excerpt="证据内容",
        line_start=4,
        line_end=8,
        page=2,
    )

    records = await store.list_for_run()
    assert records[0].artifact_id == artifact.artifact_id
    assert result["display_name"] == "上传材料.pdf"
