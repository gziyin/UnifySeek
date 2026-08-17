from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.agent_executor import AgentResearchExecutor
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.report_submitter import submit_research_report_impl


def _degraded_report_data(reason: str = "budget_exceeded: max_tool_calls") -> dict:
    """与 agent_executor._write_degraded_report 构造逐字段一致的降级报告数据。"""
    return {
        "title": f"[DEGRADED] {reason}",
        "executive_summary_claim_ids": ["degraded-summary"],
        "sections": [],
        "recommendations": [],
        "disagreements": [],
        "unknowns": [reason],
        "reason": reason,
    }


def _claim(claim_id: str, citation_ids: list[str], confidence: str) -> dict:
    return {
        "id": claim_id,
        "statement": f"statement {claim_id}",
        "citation_ids": citation_ids,
        "confidence": confidence,
    }


def _normal_report(*, claims: list[dict]) -> dict:
    return {
        "title": "normal report",
        "executive_summary_claim_ids": [claims[0]["id"]],
        "sections": [{"heading": "Section H", "claims": claims}],
        "disagreements": [],
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


@pytest.fixture
async def executor_env(tmp_path: Path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key="test-key",
        fake_agent_mode=False,
    )
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    conn = await connect(str(settings.db_path))
    await init_db(conn)

    session = await SessionRepository(conn).create()
    run = Run(
        session_id=session.session_id,
        request=ResearchRequest(question="测试问题：预算超限后的降级报告应为干净降级"),
    )
    await RunRepository(conn).create(run)

    paths = WorkspacePaths(settings.sessions_root)
    paths.ensure_run_layout(session.session_id, run.run_id)

    publisher = EventPublisher(EventRepository(conn))
    executor = AgentResearchExecutor(
        settings=settings,
        runs=RunRepository(conn),
        artifacts=ArtifactRepository(conn),
        evidence=EvidenceRepository(conn),
        publisher=publisher,
        paths=paths,
    )
    yield conn, run, executor
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


def _tool_start(name: str, run_id: str, input_: dict) -> dict:
    return {"event": "on_tool_start", "name": name, "run_id": run_id, "data": {"input": input_}}


def _tool_end(name: str, run_id: str, output) -> dict:
    return {"event": "on_tool_end", "name": name, "run_id": run_id, "data": {"output": output}}


class _StubAgent:
    """Minimal stand-in for the compiled deep agent graph."""

    def __init__(self, events: list[dict]):
        self._events = events

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        async def _gen():
            for ev in self._events:
                yield ev

        return _gen()


async def test_system_generated_degraded_report_passes_cleanly(env):
    """system_generated=True + 降级 report_data → 校验通过、产出 artifact、
    markdown 含 [DEGRADED] 标题与 reason、无 sidecar json。"""
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
    assert "budget_exceeded" in content
    assert "ReportValidationError" not in content
    assert artifact.normalized_storage_path is None


async def test_default_false_still_degrades(env):
    """system_generated=False（默认）+ 同样数据 → 仍降级（回归保护：参数不可用于绕过模型校验）。"""
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
    artifact = await artifacts.get(UUID(result["artifact_id"]))
    assert artifact.normalized_storage_path is None


async def test_executor_budget_exceeded_publishes_clean_degraded_report(executor_env):
    """预算超限收敛：report.ready payload degraded=true 且 reason 含 budget_exceeded
    （而非 ReportValidationError），降级 markdown 为干净降级产物。"""
    conn, run, executor = executor_env
    executor._settings.agent_max_tool_calls = 2
    executor._settings.agent_max_elapsed_seconds = 0
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end("search_web", "r1", {"items": []}),
        _tool_start("search_web", "r2", {"query": "LangGraph"}),
        _tool_end("search_web", "r2", {"items": []}),
        _tool_start("search_web", "r3", {"query": "never reached"}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "max_tool_calls" in updated.error_message

    db_events = await EventRepository(conn).list_after(run.run_id, 0)
    ready = [e for e in db_events if e.type == "report.ready"]
    assert ready
    assert ready[0].payload["degraded"] is True
    assert "budget_exceeded" in (ready[0].payload.get("reason") or "")
    assert "ReportValidationError" not in (ready[0].payload.get("reason") or "")

    artifact = await ArtifactRepository(conn).get(updated.report_artifact_id)
    assert artifact is not None
    content = Path(artifact.original_storage_path).read_text(encoding="utf-8")
    assert "[DEGRADED]" in content
    assert "budget_exceeded" in content
    assert "ReportValidationError" not in content
    assert artifact.normalized_storage_path is None


async def test_system_generated_normal_report_passes(env):
    """正常报告（sections/claims/citations）在 system_generated=True 下也必须通过（跳过逻辑不误伤）。"""
    store, artifacts, paths, session_id, run_id = env
    web_id = await _add_web_evidence(store)
    result = await submit_research_report_impl(
        store=store,
        artifacts=artifacts,
        paths=paths,
        session_id=session_id,
        run_id=run_id,
        report_data=_normal_report(claims=[_claim("C1", [web_id], "high")]),
        system_generated=True,
    )
    assert result["degraded"] is False
    artifact = await artifacts.get(UUID(result["artifact_id"]))
    content = Path(artifact.original_storage_path).read_text(encoding="utf-8")
    assert "normal report" in content
    assert "Section H" in content
