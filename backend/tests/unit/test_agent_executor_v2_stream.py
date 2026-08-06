from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_dev_researcher.agents.context import RunContext
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
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.knowledge_index import KbChunk
from ai_dev_researcher.storage.paths import WorkspacePaths


def _tool_start(name: str, run_id: str, input_: dict) -> dict:
    return {"event": "on_tool_start", "name": name, "run_id": run_id, "data": {"input": input_}}


def _tool_end(name: str, run_id: str, output) -> dict:
    return {"event": "on_tool_end", "name": name, "run_id": run_id, "data": {"output": output}}


def _model_end(content: str) -> dict:
    return {
        "event": "on_chat_model_end",
        "name": "ChatDeepSeek",
        "data": {"output": {"content": content}},
    }


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
    """三次流结束都没有 submit_research_report：run 应 FAILED 且生成降级报告。"""
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
    assert "after two controlled retries" in updated.error_message
    assert updated.report_artifact_id is not None

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.failed" in types


class _SequenceStubAgent:
    """Returns a different event batch per astream_events call."""

    def __init__(self, batches: list[list[dict]]):
        self._batches = batches
        self._calls = 0
        self._configs: list[dict] = []

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self._calls += 1
        self._configs.append(kwargs.get("config") or {})
        events = self._batches[min(self._calls, len(self._batches)) - 1] if self._batches else []
        async def _gen():
            for ev in events:
                yield ev
        return _gen()


class _InterruptMidStreamStubAgent:
    def __init__(self, runs, run_id):
        self._runs = runs
        self._run_id = run_id

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        async def _gen():
            await self._runs.update_status(
                self._run_id,
                RunStatus.INTERRUPTED,
                finished=True,
                error_code="SERVER_RESTART",
                error_message="Run interrupted by server restart",
            )
            raise RuntimeError("failure after run was interrupted")

        return _gen()


class _FakeKnowledgeIndex:
    is_ready = True

    def __init__(self, chunks: list[dict]):
        self._chunks = chunks

    def retrieve(self, query: str, path=None, top_k: int = 10, score_threshold: float = 0.0):  # noqa: ANN001, ANN002, ANN003
        return [KbChunk(**item) for item in self._chunks]


@pytest.mark.asyncio
async def test_executor_budget_max_tool_calls_writes_degraded_report(env, tmp_path):
    """工具调用数超限：停止漫游、写 DEGRADED 报告、run FAILED with BUDGET_EXCEEDED。"""
    settings, conn, session, run, publisher, executor = env
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
    assert updated.report_artifact_id is not None

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.failed" in types


@pytest.mark.asyncio
async def test_executor_budget_constraints_override_settings(env, tmp_path):
    """run constraints 可传 max_tool_calls，护栏在请求级生效。"""
    settings, conn, session, run, publisher, executor = env
    run2 = Run(
        session_id=session.session_id,
        request=ResearchRequest(
            question="测试问题：通过约束传递预算上限",
            constraints=["max_tool_calls=1"],
        ),
    )
    await RunRepository(conn).create(run2)
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end("search_web", "r1", {"items": []}),
        _tool_start("search_web", "r2", {"query": "never reached"}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run2.run_id)

    updated = await RunRepository(conn).get(run2.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "max_tool_calls" in updated.error_message


@pytest.mark.asyncio
async def test_executor_missing_submit_retries_once_then_succeeds(env, tmp_path):
    """流结束未提交：保留 evidence、重置 thread 重试一次；第二次提交后成功。"""
    settings, conn, session, run, publisher, executor = env
    first_batch = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end("search_web", "r1", {"items": [{"evidence_id": "S1"}]}),
    ]
    second_batch = [
        _tool_start("submit_research_report", "r2", {"title": "retry"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "retry"}),
    ]
    stub = _SequenceStubAgent([first_batch, second_batch])

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert str(updated.report_artifact_id) == ARTIFACT_ID
    assert stub._calls == 2
    assert ":retry" in str(stub._configs[1].get("configurable", {}).get("thread_id"))


@pytest.mark.asyncio
async def test_executor_missing_submit_thrice_writes_degraded_report_with_last_message(env):
    """三次 attempt 均未提交：run FAILED + 降级报告，失败原因与报告含最后模型消息。"""
    settings, conn, session, run, publisher, executor = env
    batches = [
        [_model_end("第一轮：我先继续搜索证据。")],
        [_model_end("第二轮：证据已够，以下是结论……")],
        [_model_end("第三轮：最终回答文本（未调用提交工具）。")],
    ]
    stub = _SequenceStubAgent(batches)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert "after two controlled retries" in updated.error_message
    assert "last assistant message" in updated.error_message
    assert updated.report_artifact_id is not None

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.failed" in types

    artifact = await ArtifactRepository(conn).get(updated.report_artifact_id)
    assert artifact is not None
    content = Path(artifact.original_storage_path).read_text(encoding="utf-8")
    assert "第三轮：最终回答文本（未调用提交工具）。" in content


@pytest.mark.asyncio
async def test_executor_missing_submit_thrice_then_succeeds_on_third(env):
    """第三次 attempt 成功提交：run SUCCEEDED 且使用 :retry2 thread。"""
    settings, conn, session, run, publisher, executor = env
    batches = [
        [_model_end("第一轮文本")],
        [_model_end("第二轮文本")],
        [
            _tool_start("submit_research_report", "r3", {"title": "final"}),
            _tool_end("submit_research_report", "r3", {"artifact_id": ARTIFACT_ID, "title": "final"}),
        ],
    ]
    stub = _SequenceStubAgent(batches)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert str(updated.report_artifact_id) == ARTIFACT_ID
    assert stub._calls == 3
    assert ":retry2" in str(stub._configs[2].get("configurable", {}).get("thread_id"))


@pytest.mark.asyncio
async def test_executor_budget_on_second_attempt_stops_before_third(env):
    """第二次 attempt 预算超限：立即 BUDGET_EXCEEDED，不再发起第三次 attempt。"""
    settings, conn, session, run, publisher, executor = env
    executor._settings.agent_max_tool_calls = 1
    executor._settings.agent_max_elapsed_seconds = 0
    batches = [
        [_model_end("第一轮文本")],
        [_tool_start("search_web", "r2", {"query": "budget"})],
    ]
    stub = _SequenceStubAgent(batches)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert updated.report_artifact_id is not None
    assert stub._calls == 2


@pytest.mark.asyncio
async def test_executor_preserves_interrupted_when_failure_occurs_mid_run(env):
    """run 中途被接管标为 interrupted 后失败：保留 interrupted，不再抛 invalid transition。"""
    settings, conn, session, run, publisher, executor = env
    runs_repo = RunRepository(conn)
    stub = _InterruptMidStreamStubAgent(runs_repo, run.run_id)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await runs_repo.get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.INTERRUPTED
    assert updated.error_code == "SERVER_RESTART"
    assert "run.failed" not in await _event_types(conn, run.run_id)


@pytest.mark.asyncio
async def test_executor_kb_prefetch_injects_context_and_evidence(env, tmp_path):
    """KB 兜底：不依赖模型委托，确定性检索并写入 K 类证据与 orchestrator 上下文。"""
    settings, conn, session, run, publisher, executor = env
    kb_root = tmp_path / "kb"
    kb_root.mkdir(parents=True)
    (kb_root / "notes.md").write_text(
        "# Notes\n\nDeepAgents uses explicit graph orchestration with subagents.\n",
        encoding="utf-8",
    )
    settings.knowledge_base_root = kb_root
    fake_index = _FakeKnowledgeIndex(
        [
            {
                "file_path": "notes.md",
                "symbol": "orchestration",
                "parent_symbol": "",
                "kind": "doc",
                "line_start": 1,
                "line_end": 3,
                "score": 0.9,
                "text": "DeepAgents uses explicit graph orchestration with subagents.",
            }
        ]
    )
    executor._knowledge_index = fake_index
    captured = {}
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end("search_web", "r1", {"items": [{"evidence_id": "S1", "title": "DeepAgents", "url": "https://x"}]}),
        _tool_start("submit_research_report", "r2", {"title": "t"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None):
        captured["context"] = context
        captured["knowledge_index"] = knowledge_index
        return stub

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        side_effect=_fake_create,
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert captured["knowledge_index"] is fake_index
    assert "notes.md" in captured["context"].knowledge_context

    store = EvidenceStore(
        run_id=run.run_id,
        session_id=run.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths_for_test(settings),
    )
    ledger = await store.list_for_run()
    assert any(item.source_type == "knowledge_base" for item in ledger)

    types = await _event_types(conn, run.run_id)
    assert "source.discovered" in types
    assert "evidence.recorded" in types


def paths_for_test(settings: Settings) -> WorkspacePaths:
    return WorkspacePaths(settings.sessions_root)
@pytest.mark.asyncio
async def test_executor_budget_reason_elapsed(env):
    """预算判断逻辑：max_elapsed_seconds 超限可归因。"""
    settings, conn, session, run, publisher, executor = env
    context = RunContext(
        run_id=run.run_id,
        session_id=run.session_id,
        question=run.request.question,
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=WorkspacePaths(settings.sessions_root),
        settings=settings,
        max_tool_calls=0,
        max_elapsed_seconds=1.0,
    )
    assert executor._budget_reason(0, 2.0, context) == "budget_exceeded: max_elapsed_seconds"
