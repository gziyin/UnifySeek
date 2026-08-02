from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.domain.runs import ResearchRequest, Run, RunStatus
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.agent_executor import AgentResearchExecutor
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.storage.paths import WorkspacePaths


def _tool_start(name: str, run_id: str, input_: dict) -> dict:
    return {"event": "on_tool_start", "name": name, "run_id": run_id, "data": {"input": input_}}


def _tool_end(name: str, run_id: str, output) -> dict:
    return {"event": "on_tool_end", "name": name, "run_id": run_id, "data": {"output": output}}


ARTIFACT_ID = "851a4589-edee-470e-9732-0ee5548fa5b7"


class _StubAgent:
    """Minimal stand-in for the compiled deep agent graph."""

    def __init__(self, events: list[dict]):
        self._events = events

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        async def _gen():
            for ev in self._events:
                yield ev

        return _gen()


@pytest.fixture
async def env(tmp_path: Path):
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
        request=ResearchRequest(question="测试问题：对比两个框架的编排差异以验证事件流"),
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
    yield settings, conn, session, run, publisher, executor
    await conn.close()


async def _event_types(conn, run_id) -> list[str]:
    events = await EventRepository(conn).list_after(run_id, 0)
    return [e.type for e in events]


@pytest.mark.asyncio
async def test_executor_v2_stream_success_path(env):
    """v2 事件流：search_web -> submit_research_report，run 应 SUCCEEDED。"""
    settings, conn, session, run, publisher, executor = env
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end(
            "search_web",
            "r1",
            {"items": [{"evidence_id": "S1", "title": "DeepAgents", "url": "https://x", "evidence_level": "search_snippet"}]},
        ),
        _tool_start("submit_research_report", "r2", {"report": {"title": "t"}}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert str(updated.report_artifact_id) == ARTIFACT_ID

    types = await _event_types(conn, run.run_id)
    assert "source.discovered" in types
    assert "evidence.recorded" in types
    assert "report.ready" in types
    assert "run.succeeded" in types


@pytest.mark.asyncio
async def test_executor_v2_stream_degraded_path(env):
    """降级报告：run 应 FAILED 但保留 artifact_id。"""
    settings, conn, session, run, publisher, executor = env
    events = [
        _tool_start("submit_research_report", "r2", {"report": {"title": "t"}}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "degraded": True, "reason": "bad citations"}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert str(updated.report_artifact_id) == ARTIFACT_ID
    assert "degraded" in updated.error_message

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.failed" in types


@pytest.mark.asyncio
async def test_executor_v2_stream_no_submit_fails(env):
    """流结束但没有 submit_research_report：run 应 FAILED。"""
    settings, conn, session, run, publisher, executor = env
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end("search_web", "r1", {"items": [{"evidence_id": "S1", "title": "DeepAgents", "url": "https://x", "evidence_level": "search_snippet"}]}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert "without submit_research_report" in updated.error_message
