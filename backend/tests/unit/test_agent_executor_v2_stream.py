from __future__ import annotations

import asyncio
import time
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
from ai_dev_researcher.tools.factory import create_document_tools
from ai_dev_researcher.tools.knowledge_base import KbToolBudget, search_knowledge_base_impl


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


def _explore_events(count: int, prefix: str = "s") -> list[dict]:
    """Batch C：生成 count 次 search_web 的 start/end 事件（探索性工具）。"""
    events: list[dict] = []
    for i in range(count):
        rid = f"{prefix}{i}"
        events.append(_tool_start("search_web", rid, {"query": "q"}))
        events.append(_tool_end("search_web", rid, {"items": []}))
    return events


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
async def test_executor_search_web_publishes_all_discovered_items(env):
    """#26/#E：一次 search_web 返回 3 条 items → 恰 3 组 source.discovered(web) +
    3 组 evidence.recorded，evidence_id 集合一致（不再 last-wins 只留最后一条）。"""
    settings, conn, session, run, publisher, executor = env
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end(
            "search_web",
            "r1",
            {
                "items": [
                    {"evidence_id": "S1", "title": "a", "url": "https://a", "evidence_level": "search_snippet"},
                    {"evidence_id": "S2", "title": "b", "url": "https://b", "evidence_level": "search_snippet"},
                    {"evidence_id": "S3", "title": "c", "url": "https://c", "evidence_level": "search_snippet"},
                ]
            },
        ),
        _tool_start("submit_research_report", "r2", {"title": "t"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED

    db_events = await EventRepository(conn).list_after(run.run_id, 0)
    web_discovered = [
        e
        for e in db_events
        if e.type == "source.discovered" and e.payload.get("source_type") == "web"
    ]
    web_recorded = [
        e
        for e in db_events
        if e.type == "evidence.recorded" and e.payload.get("source_type") == "web"
    ]
    assert len(web_discovered) == 3
    assert len(web_recorded) == 3
    assert {e.payload.get("evidence_id") for e in web_discovered} == {"S1", "S2", "S3"}
    assert {e.payload.get("evidence_id") for e in web_recorded} == {"S1", "S2", "S3"}


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


class _HangingStreamAgent:
    """astream_events 永不产出事件（模拟模型/工具调用挂起，且无事件触发预算检查）。

    必须是真正的 async generator（含 yield），否则 astream_events 返回协程，
    executor 会先 ``await`` 它而直接挂死，测不到 idle timeout。
    """

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        async def _gen():
            if False:
                yield  # 使函数成为 async generator（永不真正产出事件）
            await asyncio.sleep(3600)

        return _gen()


class _SlowStreamAgent:
    """按批返回事件，批次间可人为 sleep（用于触发阶段预算/空闲超时）。"""

    def __init__(self, batches: list[list[dict]], gap: float = 0.0):
        self._batches = batches
        self._gap = gap
        self._calls = 0

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self._calls += 1
        events = self._batches[min(self._calls, len(self._batches)) - 1] if self._batches else []

        async def _gen():
            for ev in events:
                yield ev
                if self._gap:
                    await asyncio.sleep(self._gap)

        return _gen()


class _SlowSyncKnowledgeIndex:
    """retrieve 同步阻塞 0.5s（模拟 embed/chroma 卡住）——检验 to_thread offload 与 wait_for 可打断。"""

    is_ready = True

    def retrieve(self, query, path=None, top_k: int = 10, score_threshold: float = 0.0):  # noqa: ANN001, ANN002, ANN003
        time.sleep(0.5)
        return []


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
    """Settings 正数作为全局收紧上限时，探索达上限（max_tool_calls-2）：停止漫游、
    写 DEGRADED 报告、run FAILED with BUDGET_EXCEEDED。"""
    settings, conn, session, run, publisher, executor = env
    # medium profile 40 被 settings 4 收紧（min(40, 4)=4）→ 探索预算 = 4-2 = 2。
    executor._settings.agent_max_tool_calls = 4
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
    assert "exploration_budget" in updated.error_message
    assert updated.report_artifact_id is not None

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.failed" in types


@pytest.mark.asyncio
async def test_executor_budget_constraints_override_settings(env, tmp_path):
    """run constraints 可传 max_tool_calls，护栏在请求级生效（探索预算 = max-2）。"""
    settings, conn, session, run, publisher, executor = env
    run2 = Run(
        session_id=session.session_id,
        request=ResearchRequest(
            question="测试问题：通过约束传递预算上限",
            constraints=["max_tool_calls=3"],
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
    assert "exploration_budget" in updated.error_message


@pytest.mark.asyncio
async def test_executor_output_mode_wires_profile_budget_into_context(env):
    """output_mode 注入 RunContext：max_tool_calls/max_elapsed 取 profile 初始值（short=24/120s）。"""
    settings, conn, session, run, publisher, executor = env
    run2 = Run(
        session_id=session.session_id,
        request=ResearchRequest(question="短调研", output_mode="short"),
    )
    await RunRepository(conn).create(run2)
    captured = {}
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end("search_web", "r1", {"items": [{"evidence_id": "S1", "url": "https://x"}]}),
        _tool_start("submit_research_report", "r2", {"title": "t"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None, kb_budget=None):
        captured["context"] = context
        captured["kb_budget"] = kb_budget
        return stub

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        side_effect=_fake_create,
    ):
        await executor(run2.run_id)

    updated = await RunRepository(conn).get(run2.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    context = captured["context"]
    assert context.output_mode.value == "short"
    # short profile：24 工具 / 120s / KB 6。
    assert context.max_tool_calls == 24
    assert context.max_elapsed_seconds == 120.0
    assert captured["kb_budget"].limit == 6


@pytest.mark.asyncio
async def test_executor_default_output_mode_medium_profile(env):
    """默认 output_mode=medium：profile 预算 40 工具 / 300s / KB 12 经 DI 注入。"""
    settings, conn, session, run, publisher, executor = env
    captured = {}
    events = [
        _tool_start("submit_research_report", "r2", {"title": "t"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None, kb_budget=None):
        captured["context"] = context
        captured["kb_budget"] = kb_budget
        return stub

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        side_effect=_fake_create,
    ):
        await executor(run.run_id)

    context = captured["context"]
    assert context.output_mode.value == "medium"
    assert context.max_tool_calls == 40
    assert context.max_elapsed_seconds == 300.0
    assert captured["kb_budget"].limit == 12


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
async def test_executor_exploration_budget_reserves_ledger_and_submit(env):
    """Batch C：探索达上限（max_tool_calls-2=4）后，get_evidence_ledger 与
    submit_research_report 仍可执行并成功 → run SUCCEEDED，而非 BUDGET_EXCEEDED。"""
    settings, conn, session, run, publisher, executor = env
    run2 = Run(
        session_id=session.session_id,
        request=ResearchRequest(
            question="测试问题：探索预算边界后收尾",
            constraints=["max_tool_calls=6"],
        ),
    )
    await RunRepository(conn).create(run2)
    events = [
        *_explore_events(4),
        _tool_start("get_evidence_ledger", "lg1", {}),
        _tool_end("get_evidence_ledger", "lg1", {"evidence": []}),
        _tool_start("submit_research_report", "rp1", {"title": "t"}),
        _tool_end("submit_research_report", "rp1", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run2.run_id)

    updated = await RunRepository(conn).get(run2.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert str(updated.report_artifact_id) == ARTIFACT_ID


@pytest.mark.asyncio
async def test_executor_exploration_budget_blocks_further_exploration(env):
    """Batch C：探索达上限后再发起搜索 → 立即 BUDGET_EXCEEDED，不继续、不 retry。"""
    settings, conn, session, run, publisher, executor = env
    run2 = Run(
        session_id=session.session_id,
        request=ResearchRequest(
            question="测试问题：探索超限停止漫游",
            constraints=["max_tool_calls=6"],
        ),
    )
    await RunRepository(conn).create(run2)
    events = [
        *_explore_events(4),
        _tool_start("search_web", "sX", {"query": "over budget"}),
    ]
    stub = _SequenceStubAgent([events])

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run2.run_id)

    updated = await RunRepository(conn).get(run2.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "exploration_budget" in updated.error_message
    assert updated.report_artifact_id is not None
    # 不发起 retry：第一次 attempt 即在探索超限处终止。
    assert stub._calls == 1


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
async def test_executor_kb_prefetch_injects_context_without_k_evidence(env, tmp_path):
    """B2/#18：预取命中只注入 knowledge_context；不写 K 证据、不发 KB 账本事件。"""
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

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None, kb_budget=None):
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
    # 1) 预取上下文：高相关片段仍注入 knowledge_context。
    assert "notes.md" in captured["context"].knowledge_context

    # 2) B2/#18：预取不落账本 —— 账本 0 条 K 证据。
    store = EvidenceStore(
        run_id=run.run_id,
        session_id=run.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths_for_test(settings),
    )
    ledger = await store.list_for_run()
    assert not any(item.source_type == "knowledge_base" for item in ledger)

    # 3) 无 KB 账本事件（source.discovered / evidence.recorded，source_type=knowledge_base）。
    db_events = await EventRepository(conn).list_after(run.run_id, 0)
    assert not any(
        e.type == "source.discovered" and e.payload.get("source_type") == "knowledge_base"
        for e in db_events
    )
    assert not any(
        e.type == "evidence.recorded" and e.payload.get("source_type") == "knowledge_base"
        for e in db_events
    )


@pytest.mark.asyncio
async def test_executor_kb_prefetch_filters_low_score_chunks(env, tmp_path):
    """#13/#18：低于 kb_prefetch_score_threshold 的 KB chunk 不注入上下文；
    B2/#18：预取无论高低分均不落账本、不发 KB 账本事件。"""
    settings, conn, session, run, publisher, executor = env
    kb_root = tmp_path / "kb"
    kb_root.mkdir(parents=True)
    (kb_root / "low.md").write_text("# Low\n\nIrrelevant content.\n", encoding="utf-8")
    (kb_root / "high.md").write_text("# High\n\nRelevant content.\n", encoding="utf-8")
    settings.knowledge_base_root = kb_root
    # 默认阈值 0.3：low score=0.1（无关，应被过滤），high score=0.9（相关，应保留）。
    fake_index = _FakeKnowledgeIndex(
        [
            {
                "file_path": "low.md",
                "symbol": "low",
                "parent_symbol": "",
                "kind": "doc",
                "line_start": 1,
                "line_end": 2,
                "score": 0.1,
                "text": "Irrelevant content.",
            },
            {
                "file_path": "high.md",
                "symbol": "high",
                "parent_symbol": "",
                "kind": "doc",
                "line_start": 1,
                "line_end": 2,
                "score": 0.9,
                "text": "Relevant content.",
            },
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

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None, kb_budget=None):
        captured["context"] = context
        return stub

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        side_effect=_fake_create,
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED

    # 1) 预取上下文：只注入高分 chunk，低分 chunk 不进入 knowledge_context。
    ctx = captured["context"].knowledge_context
    assert "high.md" in ctx
    assert "low.md" not in ctx

    # 2) B2/#18：预取不落账本 —— 高低分 chunk 均不入账本（0 条 K 证据）。
    store = EvidenceStore(
        run_id=run.run_id,
        session_id=run.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths_for_test(settings),
    )
    ledger = await store.list_for_run()
    assert not any(item.source_type == "knowledge_base" for item in ledger)

    # 3) 无 KB 账本事件（source.discovered / evidence.recorded，source_type=knowledge_base）。
    db_events = await EventRepository(conn).list_after(run.run_id, 0)
    assert not any(
        e.type == "source.discovered" and e.payload.get("source_type") == "knowledge_base"
        for e in db_events
    )
    assert not any(
        e.type == "evidence.recorded" and e.payload.get("source_type") == "knowledge_base"
        for e in db_events
    )


@pytest.mark.asyncio
async def test_executor_prefetch_not_ledger_but_model_record_writes_k(env, tmp_path):
    """B2/#18：预取命中只注入上下文（不落账本）；模型显式经
    record_knowledge_base_evidence 记录后才把 K 证据写入账本。"""
    settings, conn, session, run, publisher, executor = env
    kb_root = tmp_path / "kb"
    kb_root.mkdir(parents=True)
    (kb_root / "notes.md").write_text(
        "# Notes\n\nDeepAgents uses explicit graph orchestration with subagents.\n",
        encoding="utf-8",
    )
    settings.knowledge_base_root = kb_root
    executor._knowledge_index = _FakeKnowledgeIndex(
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
    captured = {}
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end("search_web", "r1", {"items": [{"evidence_id": "S1", "title": "DeepAgents", "url": "https://x"}]}),
        _tool_start("submit_research_report", "r2", {"title": "t"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None, kb_budget=None):
        captured["context"] = context
        return stub

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        side_effect=_fake_create,
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    context = captured["context"]
    assert "notes.md" in context.knowledge_context

    store = EvidenceStore(
        run_id=run.run_id,
        session_id=run.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths_for_test(settings),
    )
    # 预取阶段：账本无 K 证据。
    assert not any(item.source_type == "knowledge_base" for item in await store.list_for_run())

    # 模型确认相关 → 先经 search_knowledge_base 命中注册候选（同 run 搜索），再记录 K 证据。
    tools = {
        t.name: t
        for t in create_document_tools(
            context,
            store=store,
            artifacts=object(),
            knowledge_index=executor._knowledge_index,
            kb_budget=KbToolBudget(12),
        )
    }
    found = await tools["search_knowledge_base"].ainvoke({"query": "DeepAgents"})
    assert found["count"] == 1
    result = await tools["record_knowledge_base_evidence"].ainvoke(
        {
            "path": "notes.md",
            "title": "orchestration",
            "excerpt": "DeepAgents uses explicit graph orchestration with subagents.",
            "line_start": 1,
            "line_end": 3,
        }
    )
    assert result["evidence_id"].startswith("K")
    ledger = await store.list_for_run()
    assert any(item.source_type == "knowledge_base" for item in ledger)


@pytest.mark.asyncio
async def test_executor_kb_prefetch_timeout_skips_without_blocking(env):
    """prefetch 超时保护：search_knowledge_base_impl 挂起/超时时，_prefetch_knowledge
    快速返回、不记录证据、不发布账本事件、不阻塞 run 启动。"""
    settings, conn, session, run, publisher, executor = env
    executor._knowledge_index = object()  # 满足非 None，走检索路径
    store = EvidenceStore(
        run_id=run.run_id,
        session_id=run.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths_for_test(settings),
    )
    context = RunContext(
        run_id=run.run_id,
        session_id=run.session_id,
        question=run.request.question,
        uploaded_artifact_ids=run.request.uploaded_artifact_ids,
        max_web_sources=run.request.max_web_sources,
        constraints=run.request.constraints,
        focus_areas=run.request.focus_areas,
        paths=paths_for_test(settings),
        settings=settings,
        max_tool_calls=10,
        max_elapsed_seconds=100,
    )

    async def _hanging_search(**kwargs):
        raise asyncio.TimeoutError

    with patch(
        "ai_dev_researcher.services.agent_executor.search_knowledge_base_impl",
        side_effect=_hanging_search,
    ):
        await executor._prefetch_knowledge(context, store)

    # 不抛异常、正常返回（run 可继续）。
    # 不记录任何 knowledge_base 证据。
    ledger = await store.list_for_run()
    assert not any(item.source_type == "knowledge_base" for item in ledger)
    # 不发布 source.discovered / evidence.recorded 账本事件。
    events = await EventRepository(conn).list_after(run.run_id, 0)
    assert not any(
        e.type in {"source.discovered", "evidence.recorded"}
        and e.payload.get("source_type") == "knowledge_base"
        for e in events
    )
    # 预取上下文置空。
    assert context.knowledge_context == ""


def paths_for_test(settings: Settings) -> WorkspacePaths:
    return WorkspacePaths(settings.sessions_root)
@pytest.mark.asyncio
async def test_executor_budget_reason_elapsed(env):
    """预算判断逻辑：max_elapsed_seconds 超限可归因。"""
    settings, conn, session, run, publisher, executor = env
    context = RunContext(
        run_id=run.run_id,
        session_id=session.session_id,
        question=run.request.question,
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=WorkspacePaths(settings.sessions_root),
        settings=settings,
        max_tool_calls=0,
        max_elapsed_seconds=1.0,
    )
    assert executor._budget_reason(0, 2.0, context) == "budget_exceeded: max_elapsed_seconds"


@pytest.mark.asyncio
async def test_executor_idle_timeout_writes_degraded_report(env):
    """#40：事件流卡死（无事件）时，空闲超时触发收敛为 FAILED/BUDGET_EXCEEDED + DEGRADED 报告。"""
    settings, conn, session, run, publisher, executor = env
    executor._settings.agent_max_elapsed_seconds = 0
    executor._settings.agent_plan_timeout_seconds = 0
    executor._settings.agent_research_timeout_seconds = 0
    executor._settings.agent_report_timeout_seconds = 0
    executor._settings.agent_idle_timeout_seconds = 0.05
    stub = _HangingStreamAgent()

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "idle_timeout" in updated.error_message
    assert updated.finished_at is not None
    assert updated.report_artifact_id is not None


@pytest.mark.asyncio
async def test_executor_stage_budget_research_timeout_writes_degraded(env):
    """阶段级看门狗：research 阶段超时（而非总预算）触发收敛。"""
    settings, conn, session, run, publisher, executor = env
    executor._settings.agent_max_elapsed_seconds = 0
    executor._settings.agent_plan_timeout_seconds = 0
    executor._settings.agent_research_timeout_seconds = 0.1
    executor._settings.agent_report_timeout_seconds = 0
    executor._settings.agent_idle_timeout_seconds = 0  # 不启用空闲超时
    # 同一 attempt 内：r1 使 plan→research，间隔 0.15s 后 r2 触发 research 阶段预算。
    batches = [
        [
            _tool_start("search_web", "r1", {"query": "DeepAgents"}),
            _tool_start("search_web", "r2", {"query": "LangGraph"}),
        ],
    ]
    stub = _SlowStreamAgent(batches, gap=0.15)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "stage_timeout:research" in updated.error_message
    assert updated.report_artifact_id is not None


@pytest.mark.asyncio
async def test_search_knowledge_base_impl_offloads_sync_retrieve():
    """同步阻塞不可打断回归：index.retrieve 同步卡 0.5s 时，wait_for 仍能在 0.1s 触发超时。"""
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            search_knowledge_base_impl(query="q", knowledge_index=_SlowSyncKnowledgeIndex()),
            timeout=0.1,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 0.4  # 未被 0.5s 同步阻塞拖住（offload 前该断言失败且循环被冻结）


@pytest.mark.asyncio
async def test_prefetch_sync_blocking_offloaded_keeps_loop_responsive(env, tmp_path):
    """#40：prefetch 的同步检索 offload 到线程后，事件循环不被冻结、超时能跳过、不阻塞 run 启动。"""
    settings, conn, session, run, publisher, executor = env
    executor._knowledge_index = _SlowSyncKnowledgeIndex()
    executor._settings.kb_prefetch_timeout_seconds = 0.1
    store = EvidenceStore(
        run_id=run.run_id,
        session_id=run.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths_for_test(settings),
    )
    context = RunContext(
        run_id=run.run_id,
        session_id=run.session_id,
        question=run.request.question,
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths_for_test(settings),
        settings=settings,
        max_tool_calls=10,
        max_elapsed_seconds=100,
    )

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    ticker_task = asyncio.create_task(_ticker())
    started = time.monotonic()
    await executor._prefetch_knowledge(context, store)
    elapsed = time.monotonic() - started
    ticker_task.cancel()

    assert elapsed < 0.4  # 0.1s 超时即跳过，未被 0.5s 同步检索拖住
    assert context.knowledge_context == ""
    assert ticks >= 1  # 事件循环未被同步阻塞（ticker 一直在跑）
    ledger = await store.list_for_run()
    assert not any(item.source_type == "knowledge_base" for item in ledger)
