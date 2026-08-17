from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.reports import ReportSection, ResearchClaim, ResearchReport
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.artifacts import _build_numbering, collect_claims, render_report_markdown
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.report_submitter import submit_research_report_impl


def _claim(cid: str, cids: list[str], confidence: str = "medium") -> ResearchClaim:
    return ResearchClaim(id=cid, statement=f"statement {cid}", citation_ids=cids, confidence=confidence)


def _claim_dict(cid: str, cids: list[str], confidence: str = "medium") -> dict:
    return {"id": cid, "statement": f"statement {cid}", "citation_ids": cids, "confidence": confidence}


def _evidence(
    eid: str, *, url: str | None = None, locator: str | None = None
) -> EvidenceRecord:
    return EvidenceRecord(
        id=eid,
        run_id=uuid4(),
        source_type="web",
        evidence_level="first_party",
        title=f"title {eid}",
        locator=locator or url or f"locator-{eid}",
        canonical_url=url,
        excerpt=f"excerpt {eid}",
    )


def _evidence_map() -> dict[str, EvidenceRecord]:
    return {
        "S1": _evidence("S1", url="https://example.com/1"),
        "S2": _evidence("S2", url="https://example.com/2"),
        "S5": _evidence("S5", url="https://example.com/5"),
    }


def _degraded_report_data(reason: str = "budget_exceeded: max_tool_calls") -> dict:
    """与 agent_executor._write_degraded_report 构造逐字段一致（无 summary_claims）。"""
    return {
        "title": f"[DEGRADED] {reason}",
        "executive_summary_claim_ids": ["degraded-summary"],
        "sections": [],
        "recommendations": [],
        "disagreements": [],
        "unknowns": [reason],
        "reason": reason,
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


async def _add_web_evidence(store: EvidenceStore, *, level: str = "first_party") -> str:
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


# ---- 用例 1-3：schema 合法性与结构校验 ----

def test_schema_with_summary_claims_valid():
    report = ResearchReport(
        title="t",
        summary_claims=[
            _claim("SUM1", ["S1"]),
            _claim("SUM2", ["S2"], "low"),
        ],
        sections=[ReportSection(heading="H", claims=[_claim("C1", ["S1"])])],
        recommendations=[_claim("CR", ["S1"])],
    )
    assert report.summary_claims[0].id == "SUM1"
    assert report.summary_claims[1].confidence == "low"


def test_schema_legacy_valid_and_empties_ok():
    legacy = ResearchReport(
        title="t",
        executive_summary_claim_ids=["C1"],
        sections=[ReportSection(heading="H", claims=[_claim("C1", ["S1"])])],
        recommendations=[_claim("CR", ["S1"])],
    )
    assert legacy.executive_summary_claim_ids == ["C1"]
    assert legacy.summary_claims == []
    empty_both = ResearchReport(
        title="t",
        sections=[],
        recommendations=[],
    )
    assert empty_both.summary_claims == []
    assert empty_both.executive_summary_claim_ids == []


def test_schema_summary_claim_requires_citations():
    with pytest.raises(ValidationError):
        _claim("S-BAD", [])


# ---- 用例 4-7：collect_claims / _build_numbering / render ----

def test_collect_claims_includes_summary_claims():
    report = ResearchReport(
        title="t",
        summary_claims=[_claim("SUM1", ["S5"])],
        sections=[ReportSection(heading="H", claims=[_claim("C1", ["S1"])])],
        recommendations=[_claim("CR", ["S2"])],
    )
    claims = collect_claims(report)
    assert set(claims.keys()) == {"SUM1", "C1", "CR"}
    assert claims["SUM1"].statement == "statement SUM1"


def test_build_numbering_summary_claims_first_and_priority():
    hybrid = ResearchReport(
        title="t",
        summary_claims=[_claim("SUM1", ["S5"]), _claim("SUM2", ["S1"])],
        executive_summary_claim_ids=["C1"],
        sections=[ReportSection(heading="H", claims=[_claim("C1", ["S1"]), _claim("C2", ["S2"])])],
        recommendations=[_claim("CR", ["S2"])],
    )
    num = _build_numbering(hybrid, collect_claims(hybrid))
    # summary_claims 优先：S5(1) → S1(2)；exec ids 被忽略；S2(3)
    assert num == {"S5": 1, "S1": 2, "S2": 3}

    legacy = ResearchReport(
        title="t",
        executive_summary_claim_ids=["C1"],
        sections=[ReportSection(heading="H", claims=[_claim("C1", ["S1"])])],
        recommendations=[_claim("CR", ["S2"])],
    )
    num = _build_numbering(legacy, collect_claims(legacy))
    assert num == {"S1": 1, "S2": 2}


def test_render_summary_claims_priority():
    report = ResearchReport(
        title="t",
        summary_claims=[_claim("SUM1", ["S5"]), _claim("SUM2", ["S1"])],
        executive_summary_claim_ids=["C1"],
        sections=[ReportSection(heading="H", claims=[_claim("C1", ["S1"])])],
        recommendations=[_claim("CR", ["S2"])],
    )
    md = render_report_markdown(report, collect_claims(report), _evidence_map())
    core = md.split("## 核心结论", 1)[1].split("## ", 1)[0]
    assert "statement SUM1" in core
    assert "statement SUM2" in core
    assert "statement C1" not in core  # fallback 旧句不出现
    assert "*来源：[1][2]*" in core  # S5=1, S1=2
    assert "# [DEGRADED]" not in md


def test_render_fallback_to_exec_ids():
    report = ResearchReport(
        title="t",
        executive_summary_claim_ids=["C1"],
        sections=[ReportSection(heading="H", claims=[_claim("C1", ["S1"])])],
        recommendations=[_claim("CR", ["S2"])],
    )
    md = render_report_markdown(report, collect_claims(report), _evidence_map())
    core = md.split("## 核心结论", 1)[1].split("## ", 1)[0]
    assert "statement C1" in core


# ---- 用例 8-9：跨校验经通用 registry（不改 report_submitter）----

async def _submit(env, report_data: dict, *, system_generated: bool = False) -> dict:
    store, artifacts, paths, session_id, run_id = env
    return await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=report_data,
        system_generated=system_generated,
    )


def _summary_report_data(*, summary_citation_ids: list[str], valid_section_id: str) -> dict:
    return {
        "title": "t",
        "summary_claims": [_claim_dict("SUM1", summary_citation_ids, "medium")],
        "sections": [{"heading": "H", "claims": [_claim_dict("C1", [valid_section_id], "medium")]}],
        "recommendations": [],
        "disagreements": [],
        "unknowns": [],
    }


async def test_summary_claims_unknown_evidence_degrades(env):
    web_id = await _add_web_evidence(env[0])
    result = await _submit(env, _summary_report_data(summary_citation_ids=["S99"], valid_section_id=web_id))
    assert result["degraded"] is True
    assert "references unknown evidence" in (result["reason"] or "")


async def test_summary_claims_valid_passes_and_sidecar(env):
    store, artifacts, paths, session_id, run_id = env
    web_id = await _add_web_evidence(store)
    result = await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=_summary_report_data(summary_citation_ids=[web_id], valid_section_id=web_id),
    )
    assert result["degraded"] is False
    artifact = await artifacts.get(UUID(result["artifact_id"]))
    sidecar = json.loads(Path(artifact.normalized_storage_path).read_text(encoding="utf-8"))
    assert sidecar["summary_claims"][0]["id"] == "SUM1"


# ---- 用例 10-11：降级路径回归（#44 不受 schema 变化影响）----

async def test_degraded_data_system_generated_still_passes(env):
    store, artifacts, paths, session_id, run_id = env
    result = await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=_degraded_report_data(),
        system_generated=True,
    )
    assert result["degraded"] is False
    artifact = await artifacts.get(UUID(result["artifact_id"]))
    content = Path(artifact.original_storage_path).read_text(encoding="utf-8")
    assert "[DEGRADED]" in content
    assert "ReportValidationError" not in content
    assert artifact.normalized_storage_path is None


async def test_degraded_data_default_still_degrades(env):
    store, artifacts, paths, session_id, run_id = env
    result = await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=_degraded_report_data(),
    )
    assert result["degraded"] is True
    assert "unknown summary claim" in (result["reason"] or "")