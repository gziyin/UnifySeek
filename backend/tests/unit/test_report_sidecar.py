from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.reports import ResearchReport
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.report_submitter import submit_research_report_impl


def _claim(claim_id: str, citation_ids: list[str], confidence: str) -> dict:
    return {
        "id": claim_id,
        "statement": f"statement {claim_id}",
        "citation_ids": citation_ids,
        "confidence": confidence,
    }


def _report(*, claims: list[dict], disagreements: list[dict] | None = None) -> dict:
    return {
        "title": "sidecar test",
        "executive_summary_claim_ids": [claims[0]["id"]],
        "sections": [{"heading": "H", "claims": claims}],
        "disagreements": disagreements or [],
        "unknowns": ["some unknown"],
        "recommendations": [_claim("C-REC", [claims[0]["citation_ids"][0]], "medium")],
    }


@pytest.fixture
async def env(tmp_path: Path):
    settings = type("S", (), {"workspace_root": tmp_path / "workspace"})()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    paths = WorkspacePaths(settings.workspace_root)
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    session = await SessionRepository(conn).create()
    session_id = session.session_id
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    artifacts = ArtifactRepository(conn)
    yield store, artifacts, paths, session_id, run_id
    await conn.close()


async def _add_web_evidence(store: EvidenceStore) -> str:
    eid = await store.allocate_web_id()
    await store.add(
        EvidenceRecord(
            id=eid,
            run_id=store._run_id,
            source_type="web",
            evidence_level="first_party",
            title="t",
            locator="https://example.com",
            canonical_url="https://example.com",
            excerpt="excerpt",
        )
    )
    return eid


async def test_valid_report_writes_json_sidecar(env):
    store, artifacts, paths, session_id, run_id = env
    web_id = await _add_web_evidence(store)
    result = await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=_report(claims=[_claim("C1", [web_id], "medium")]),
    )
    assert result["degraded"] is False

    artifact = await artifacts.get(UUID(result["artifact_id"]))
    assert artifact.normalized_storage_path is not None
    sidecar = Path(artifact.normalized_storage_path)
    assert sidecar.exists()
    assert sidecar.name == f"report-{artifact.artifact_id}.json"

    # sidecar sits in the same directory as the markdown
    md = Path(artifact.original_storage_path)
    assert sidecar.parent == md.parent
    assert md.exists()

    # sidecar content deserializes back into a ResearchReport
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    parsed = ResearchReport.model_validate(data)
    assert parsed.title == "sidecar test"
    assert parsed.sections[0].claims[0].id == "C1"
    assert parsed.recommendations[0].id == "C-REC"


async def test_degraded_report_writes_no_sidecar(env):
    store, artifacts, paths, session_id, run_id = env
    await _add_web_evidence(store)
    result = await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=_report(claims=[_claim("C1", ["S99"], "low")]),
    )
    assert result["degraded"] is True

    artifact = await artifacts.get(UUID(result["artifact_id"]))
    assert artifact.normalized_storage_path is None
    # markdown still written on the degraded path
    assert Path(artifact.original_storage_path).exists()
