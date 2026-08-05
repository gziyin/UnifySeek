from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.report_submitter import submit_research_report_impl


def _claim(claim_id: str, citation_ids: list[str], confidence: str) -> dict:
    return {"id": claim_id, "statement": f"statement {claim_id}", "citation_ids": citation_ids, "confidence": confidence}


def _report(*, claims: list[dict], disagreements: list[dict] | None = None) -> dict:
    return {
        "title": "validation test",
        "executive_summary_claim_ids": [claims[0]["id"]],
        "sections": [{"heading": "H", "claims": claims}],
        "disagreements": disagreements or [],
        "unknowns": [],
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


async def _add_web_evidence(store: EvidenceStore, *, level: str = "search_snippet") -> str:
    eid = await store.allocate_web_id()
    await store.add(
        EvidenceRecord(
            id=eid,
            run_id=store._run_id,
            source_type="web",
            evidence_level=level,
            title="t",
            locator="https://example.com",
            canonical_url="https://example.com",
            excerpt="excerpt",
        )
    )
    return eid


async def _submit(env, report: dict) -> dict:
    store, artifacts, paths, session_id, run_id = env
    return await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=report,
    )


async def test_valid_report_passes(env):
    store = env[0]
    web_id = await _add_web_evidence(store, level="first_party")
    result = await _submit(env, _report(claims=[_claim("C1", [web_id], "high")]))
    assert result["degraded"] is False
    assert result["reason"] is None


async def test_missing_citation_degrades(env):
    store = env[0]
    await _add_web_evidence(store)
    result = await _submit(env, _report(claims=[_claim("C1", ["S99"], "low")]))
    assert result["degraded"] is True
    assert "unknown evidence" in (result["reason"] or "")


async def test_duplicate_citation_degrades(env):
    store = env[0]
    web_id = await _add_web_evidence(store, level="first_party")
    result = await _submit(env, _report(claims=[_claim("C1", [web_id, web_id], "low")]))
    assert result["degraded"] is True
    assert "more than once" in (result["reason"] or "")


async def test_high_confidence_snippet_only_auto_degrades_to_medium(env):
    store, artifacts, paths, session_id, run_id = env
    web_id = await _add_web_evidence(store, level="search_snippet")
    result = await _submit(env, _report(claims=[_claim("C1", [web_id], "high")]))
    assert result["degraded"] is False
    assert result["reason"] is None
    artifact = await artifacts.get(UUID(result["artifact_id"]))
    content = Path(artifact.original_storage_path).read_text(encoding="utf-8")
    assert "confidence=medium" in content


async def test_high_confidence_weak_secondary_auto_degrades_to_medium(env):
    store, artifacts, paths, session_id, run_id = env
    web_id = await _add_web_evidence(store, level="secondary")
    result = await _submit(env, _report(claims=[_claim("C1", [web_id], "high")]))
    assert result["degraded"] is False
    assert result["reason"] is None
    artifact = await artifacts.get(UUID(result["artifact_id"]))
    content = Path(artifact.original_storage_path).read_text(encoding="utf-8")
    assert "confidence=medium" in content


async def test_medium_confidence_secondary_passes(env):
    store = env[0]
    web_id = await _add_web_evidence(store, level="secondary")
    result = await _submit(env, _report(claims=[_claim("C1", [web_id], "medium")]))
    assert result["degraded"] is False


async def test_disagreement_unknown_citation_degrades(env):
    store = env[0]
    web_id = await _add_web_evidence(store, level="first_party")
    disagreements = [
        {
            "topic": "conflict",
            "claim_ids": ["C1"],
            "sides": [
                {"position": "a", "citation_ids": [web_id]},
                {"position": "b", "citation_ids": ["S99"]},
            ],
        }
    ]
    result = await _submit(env, _report(claims=[_claim("C1", [web_id], "low")], disagreements=disagreements))
    assert result["degraded"] is True
    assert "disagreement" in (result["reason"] or "")


async def test_report_lands_in_slug_session_dir(tmp_path: Path):
    """Issue 9: report lands inside the slug session dir, no separate UUID dir."""
    settings = type("S", (), {"workspace_root": tmp_path / "workspace"})()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    paths = WorkspacePaths(settings.workspace_root)
    conn = await connect(str(tmp_path / "app.db"))
    await init_db(conn)
    session = await SessionRepository(conn).create()
    session_id = session.session_id
    run_id = uuid4()
    display_name = "江西农业大学是个什么学校"
    # production flow: run_service names the session dir as <slug>-8hex before submit
    paths.ensure_run_layout(session_id, run_id, display_name=display_name)
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    artifacts = ArtifactRepository(conn)
    web_id = await store.allocate_web_id()
    await store.add(
        EvidenceRecord(
            id=web_id,
            run_id=run_id,
            source_type="web",
            evidence_level="first_party",
            title="t",
            locator="https://example.com",
            canonical_url="https://example.com",
            excerpt="excerpt",
        )
    )
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
    stored = Path(artifact.original_storage_path)
    slug_dir = paths.session_dir(session_id)
    assert slug_dir in stored.parents
    legacy_report_dir = paths.sessions_root / str(session_id) / "runs" / str(run_id) / "reports"
    assert not legacy_report_dir.exists()
    await conn.close()
