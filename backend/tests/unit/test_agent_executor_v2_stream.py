from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from langgraph.errors import GraphRecursionError

from ai_dev_researcher.agents.context import RunContext
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
    assert "run.succeeded" not in types


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
    assert "structured finalization failed" in updated.error_message
    assert updated.report_artifact_id is not None

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.failed" in types
    assert "run.succeeded" not in types


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


class _LedgerThenDelayedEventAgent:
    """Completes the evidence ledger, then delays the next event."""

    def __init__(self, delay: float):
        self._delay = delay

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        async def _gen():
            yield _tool_start("search_web", "search-1", {"query": "q"})
            yield _tool_end("get_evidence_ledger", "ledger-1", {"evidence": []})
            await asyncio.sleep(self._delay)
            yield _tool_start("search_web", "search-2", {"query": "q2"})

        return _gen()


class _RaiseAfterEventsStubAgent:
    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        async def _gen():
            yield _tool_start("submit_research_report", "submit", {"title": "t"})
            yield _tool_end(
                "submit_research_report",
                "submit",
                {"artifact_id": ARTIFACT_ID, "degraded": False, "title": "t"},
            )
            yield _tool_start("write_todos", "todo-1", {"todos": []})
            yield _tool_end("write_todos", "todo-1", {"items": []})
            raise GraphRecursionError(
                "Recursion limit of 25 reached without hitting a stop condition"
            )

        return _gen()


class _RecursionSequenceAgent:
    """Raises the real LangGraph recursion exception for selected attempts."""

    def __init__(self, batches: list[list[dict]], recurse_attempts: set[int]):
        self._batches = batches
        self._recurse_attempts = recurse_attempts
        self._calls = 0

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self._calls += 1
        events = self._batches[min(self._calls, len(self._batches)) - 1]
        should_recurse = self._calls in self._recurse_attempts

        async def _gen():
            for ev in events:
                yield ev
            if should_recurse:
                raise GraphRecursionError(
                    "Recursion limit of 25 reached without hitting a stop condition"
                )

        return _gen()


class _VirtualClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


class _VirtualSequenceAgent:
    def __init__(self, batches: list[list[dict]], advances: list[float], clock: _VirtualClock):
        self._batches = batches
        self._advances = advances
        self._clock = clock
        self._calls = 0

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self._calls += 1
        batch = self._batches[self._calls - 1]
        advance = self._advances[self._calls - 1]

        async def _gen():
            for event in batch:
                yield event
            self._clock.advance(advance)

        return _gen()


class _VirtualRecursionAtDeadlineAgent:
    def __init__(self, clock: _VirtualClock, advance: float):
        self._clock = clock
        self._advance = advance
        self._calls = 0

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self._calls += 1

        async def _gen():
            yield _model_end("recursing at soft deadline")
            self._clock.advance(self._advance)
            raise GraphRecursionError(
                "Recursion limit of 25 reached without hitting a stop condition"
            )

        return _gen()


class _VirtualStructuredRunnable:
    def __init__(self, report: dict, clock: _VirtualClock, advance: float):
        self._report = report
        self._clock = clock
        self._advance = advance

    async def ainvoke(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self._clock.advance(self._advance)
        return self._report


class _VirtualStructuredModel:
    def __init__(self, report: dict, clock: _VirtualClock, advance: float = 0.0):
        self._report = report
        self._clock = clock
        self._advance = advance
        self.calls = 0

    def model_copy(self, *, update=None, deep=False):  # noqa: ANN001, ANN002
        return self

    def with_structured_output(self, schema, *, method="function_calling", **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        return _VirtualStructuredRunnable(self._report, self._clock, self._advance)


class _StructuredRunnable:
    def __init__(self, report: dict, delay: float = 0.0):
        self.report = report
        self.delay = delay

    async def ainvoke(self, *args, **kwargs):  # noqa: ANN002, ANN003
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.report


class _StructuredModel:
    def __init__(
        self,
        report: dict,
        delay: float = 0.0,
        *,
        extra_body: dict | None = None,
        reject_thinking_tool_choice: bool = False,
        calls: list[dict] | None = None,
    ):
        self.report = report
        self.delay = delay
        self.extra_body = extra_body or {"thinking": {"type": "enabled"}}
        self.reject_thinking_tool_choice = reject_thinking_tool_choice
        self.calls = calls if calls is not None else []

    def model_copy(self, *, update=None, deep=False):  # noqa: ANN001, ANN002
        values = dict(update or {})
        return _StructuredModel(
            self.report,
            self.delay,
            extra_body=values.get("extra_body", self.extra_body),
            reject_thinking_tool_choice=self.reject_thinking_tool_choice,
            calls=self.calls,
        )

    def with_structured_output(self, schema, *, method="function_calling", **kwargs):  # noqa: ANN001, ANN003
        if (
            self.reject_thinking_tool_choice
            and method == "function_calling"
            and self.extra_body.get("thinking", {}).get("type") != "disabled"
        ):
            raise RuntimeError("HTTP 400: Thinking mode does not support this tool_choice")
        self.calls.append(
            {
                "schema": schema,
                "method": method,
                "kwargs": kwargs,
                "extra_body": dict(self.extra_body),
            }
        )
        return _StructuredRunnable(self.report, self.delay)


class _ModelBinding:
    def __init__(self, model):
        self.instance = model


class _FailingStructuredRunnable:
    async def ainvoke(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("structured provider unavailable")


class _FailingStructuredModel:
    def with_structured_output(self, schema, *, method="function_calling", **kwargs):  # noqa: ANN001, ANN003
        return _FailingStructuredRunnable()


@pytest.mark.asyncio
async def test_executor_returns_after_submit_completed_before_graph_recursion(env):
    settings, conn, session, run, publisher, executor = env
    stub = _RaiseAfterEventsStubAgent()

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(_FailingStructuredModel()),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    types = await _event_types(conn, run.run_id)
    assert types.count("tool.completed") == 1
    assert types.count("report.ready") == 1
    assert types.count("run.succeeded") == 1
    assert "run.failed" not in types


@pytest.mark.asyncio
async def test_executor_first_recursion_enters_structured_finalization(env):
    settings, conn, session, run, publisher, executor = env
    stub = _RecursionSequenceAgent([[_tool_start("search_web", "search-1", {"query": "q"})]], recurse_attempts={1})
    model = _StructuredModel({"title": "structured final", "sections": [], "recommendations": [], "disagreements": [], "unknowns": []})

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch("ai_dev_researcher.services.agent_executor.create_model_binding", return_value=_ModelBinding(model)),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert stub._calls == 1
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.succeeded") == 1
    assert "run.failed" not in types


@pytest.mark.asyncio
async def test_executor_two_recursions_use_structured_finalization_success(env):
    settings, conn, session, run, publisher, executor = env
    stub = _RecursionSequenceAgent([[], []], recurse_attempts={1, 2})
    model = _StructuredModel(
        {
            "title": "structured final",
            "sections": [],
            "recommendations": [],
            "disagreements": [],
            "unknowns": [],
        }
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert stub._calls == 1
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.succeeded") == 1
    assert "run.failed" not in types


@pytest.mark.asyncio
async def test_executor_first_recursion_structured_failure_keeps_single_degraded_artifact(env):
    settings, conn, session, run, publisher, executor = env
    stub = _RecursionSequenceAgent([[]], recurse_attempts={1})

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(_FailingStructuredModel()),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "RUN_FAILED"
    assert "GraphRecursionError" in (updated.error_message or "")
    assert "structured finalization failed" in (updated.error_message or "")
    artifacts = await ArtifactRepository(conn).list_for_session(session.session_id)
    assert len(artifacts) == 1
    assert updated.report_artifact_id == artifacts[0].artifact_id
    degraded_text = Path(artifacts[0].original_storage_path).read_text(encoding="utf-8")
    assert "GraphRecursionError" in degraded_text
    assert "structured finalization failed" in degraded_text
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.failed") == 1


@pytest.mark.asyncio
async def test_executor_recursion_at_soft_deadline_preserves_context_on_structured_failure(env):
    settings, conn, session, run, publisher, executor = env
    executor._settings.agent_max_elapsed_seconds = 120.0
    clock = _VirtualClock()
    stub = _VirtualRecursionAtDeadlineAgent(clock, advance=80.0)

    with (
        patch("ai_dev_researcher.services.agent_executor.time.monotonic", side_effect=clock.monotonic),
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(_FailingStructuredModel()),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "RUN_FAILED"
    assert "GraphRecursionError" in (updated.error_message or "")
    assert "structured finalization failed" in (updated.error_message or "")
    artifacts = await ArtifactRepository(conn).list_for_session(session.session_id)
    assert len(artifacts) == 1
    degraded_text = Path(artifacts[0].original_storage_path).read_text(encoding="utf-8")
    assert "GraphRecursionError" in degraded_text
    assert "structured finalization failed" in degraded_text
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.failed") == 1
    assert "run.succeeded" not in types
    assert stub._calls == 1


@pytest.mark.asyncio
async def test_executor_second_normal_attempt_hits_soft_deadline_and_finalizes(env):
    settings, conn, session, run, publisher, executor = env
    clock = _VirtualClock()
    stub = _VirtualSequenceAgent([[_model_end("first")], [_model_end("second")]], [30.0, 50.0], clock)
    model = _VirtualStructuredModel(
        {"title": "soft deadline final", "executive_summary_claim_ids": [], "sections": [], "recommendations": [], "disagreements": [], "unknowns": []},
        clock,
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.time.monotonic", side_effect=clock.monotonic),
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch("ai_dev_researcher.services.agent_executor.create_model_binding", return_value=_ModelBinding(model)),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert stub._calls == 2
    assert model.calls == 1
    assert clock.value == 80.0
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.succeeded") == 1
    assert "run.failed" not in types


@pytest.mark.asyncio
async def test_executor_structured_budget_exhaustion_writes_single_degraded_report(env):
    settings, conn, session, run, publisher, executor = env
    executor._settings.agent_max_elapsed_seconds = 90.0
    executor._settings.agent_report_timeout_seconds = 0.0
    clock = _VirtualClock()
    stub = _VirtualSequenceAgent([[_model_end("first")]], [60.0], clock)
    model = _VirtualStructuredModel(
        {"title": "late final", "executive_summary_claim_ids": [], "sections": [], "recommendations": [], "disagreements": [], "unknowns": []},
        clock,
        advance=31.0,
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.time.monotonic", side_effect=clock.monotonic),
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch("ai_dev_researcher.services.agent_executor.create_model_binding", return_value=_ModelBinding(model)),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    artifacts = await ArtifactRepository(conn).list_for_session(session.session_id)
    assert len(artifacts) == 1
    assert updated.report_artifact_id == artifacts[0].artifact_id
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.failed") == 1
    assert "run.succeeded" not in types


@pytest.mark.asyncio
async def test_executor_long_missing_submit_uses_structured_finalization_and_succeeds(env):
    settings, conn, session, run, publisher, executor = env
    run.request.output_mode = "long"
    store = EvidenceStore(
        run_id=run.run_id,
        session_id=session.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=executor._paths,
    )
    evidence_id = await store.allocate_web_id()
    await store.add(
        EvidenceRecord(
            id=evidence_id,
            run_id=run.run_id,
            source_type="web",
            evidence_level="first_party",
            title="source",
            locator="https://example.com/source",
            canonical_url="https://example.com/source",
            excerpt="source excerpt",
        )
    )
    report = {
        "title": "Long final report",
        "executive_summary_claim_ids": ["C1"],
        "sections": [
            {
                "heading": "Findings",
                "claims": [
                    {
                        "id": "C1",
                        "statement": "validated finding",
                        "citation_ids": [evidence_id],
                        "confidence": "medium",
                    }
                ],
            }
        ],
        "recommendations": [
            {
                "id": "R1",
                "statement": "validated recommendation",
                "citation_ids": [evidence_id],
                "confidence": "medium",
            }
        ],
        "disagreements": [],
        "unknowns": [],
    }
    model = _StructuredModel(report)
    stub = _SequenceStubAgent([[_model_end("first")], [_model_end("second")]])

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.report_artifact_id is not None
    assert stub._calls == 2
    assert len(model.calls) == 1
    assert model.calls[0]["method"] == "function_calling"
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.succeeded") == 1
    assert "run.failed" not in types


@pytest.mark.asyncio
async def test_structured_submit_degraded_reuses_single_artifact(env):
    settings, conn, session, run, publisher, executor = env
    report = {
        "title": "Invalid final report",
        "executive_summary_claim_ids": ["C1"],
        "sections": [{
            "heading": "Findings",
            "claims": [{
                "id": "C1",
                "statement": "unknown citation",
                "citation_ids": ["S99"],
                "confidence": "medium",
            }],
        }],
        "recommendations": [],
        "disagreements": [],
        "unknowns": [],
    }
    model = _StructuredModel(report)
    stub = _SequenceStubAgent([[_model_end("first")], [_model_end("second")]])

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    artifacts = await ArtifactRepository(conn).list_for_session(session.session_id)
    assert len(artifacts) == 1
    assert updated.report_artifact_id == artifacts[0].artifact_id
    types = await _event_types(conn, run.run_id)
    assert types.count("report.ready") == 1
    assert types.count("run.failed") == 1


@pytest.mark.asyncio
async def test_structured_finalization_uses_effective_report_timeout(env):
    settings, conn, session, run, publisher, executor = env
    run.request.output_mode = "long"
    executor._settings.agent_report_timeout_seconds = 0.2
    executor._settings.agent_max_elapsed_seconds = 0.01
    store = EvidenceStore(
        run_id=run.run_id,
        session_id=session.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=executor._paths,
    )
    evidence_id = await store.allocate_web_id()
    await store.add(EvidenceRecord(
        id=evidence_id,
        run_id=run.run_id,
        source_type="web",
        evidence_level="first_party",
        title="source",
        locator="https://example.com/source",
        canonical_url="https://example.com/source",
        excerpt="source excerpt",
    ))
    report = {
        "title": "Slow final report",
        "executive_summary_claim_ids": ["C1"],
        "sections": [{"heading": "Findings", "claims": [{
            "id": "C1", "statement": "validated finding",
            "citation_ids": [evidence_id], "confidence": "medium"
        }]}],
        "recommendations": [],
        "disagreements": [],
        "unknowns": [],
    }
    model = _StructuredModel(report, delay=0.05)
    stub = _SequenceStubAgent([[_model_end("first")], [_model_end("second")]])

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "max_elapsed_seconds" in (updated.error_message or "")
    assert updated.report_artifact_id is not None


@pytest.mark.asyncio
async def test_structured_finalization_uses_remaining_run_budget(env):
    settings, conn, session, run, publisher, executor = env
    run.request.output_mode = "long"
    executor._settings.agent_report_timeout_seconds = 0.5
    executor._settings.agent_max_elapsed_seconds = 0.15
    store = EvidenceStore(
        run_id=run.run_id,
        session_id=session.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=executor._paths,
    )
    evidence_id = await store.allocate_web_id()
    await store.add(EvidenceRecord(
        id=evidence_id,
        run_id=run.run_id,
        source_type="web",
        evidence_level="first_party",
        title="source",
        locator="https://example.com/source",
        canonical_url="https://example.com/source",
        excerpt="source excerpt",
    ))
    report = {
        "title": "Slow final report",
        "executive_summary_claim_ids": ["C1"],
        "sections": [{"heading": "Findings", "claims": [{
            "id": "C1", "statement": "validated finding",
            "citation_ids": [evidence_id], "confidence": "medium"
        }]}],
        "recommendations": [],
        "disagreements": [],
        "unknowns": [],
    }
    model = _StructuredModel(report, delay=0.1)
    stub = _SlowStreamAgent(
        [[_model_end("first")], [_model_end("second")]],
        gap=0.05,
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "max_elapsed_seconds" in (updated.error_message or "")
    assert updated.report_artifact_id is not None


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
    """Settings 正数收紧探索上限时，探索停止并进入 structured finalization。"""
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
    model = _StructuredModel(
        {
            "title": "structured final",
            "sections": [],
            "recommendations": [],
            "disagreements": [],
            "unknowns": [],
        }
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.error_code is None
    assert updated.report_artifact_id is not None

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.succeeded" in types
    assert "run.failed" not in types


@pytest.mark.asyncio
async def test_executor_budget_constraints_override_settings(env, tmp_path):
    """run constraints 的探索预算触顶后进入 structured finalization。"""
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
    model = _StructuredModel(
        {
            "title": "structured final",
            "sections": [],
            "recommendations": [],
            "disagreements": [],
            "unknowns": [],
        }
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run2.run_id)

    updated = await RunRepository(conn).get(run2.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.error_code is None
    assert updated.report_artifact_id is not None


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
async def test_executor_two_graph_attempts_then_structured_finalization_failure_writes_degraded_report(env):
    """两轮普通 graph attempt 后 structured finalization 失败：写入降级报告。"""
    settings, conn, session, run, publisher, executor = env
    batches = [
        [_model_end("第一轮：我先继续搜索证据。")],
        [_model_end("第二轮：证据已够，以下是结论……")],
        [_model_end("第三轮：最终回答文本（未调用提交工具）。")],
    ]
    stub = _SequenceStubAgent(batches)

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(_FailingStructuredModel()),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert "structured finalization failed" in updated.error_message
    assert updated.report_artifact_id is not None

    types = await _event_types(conn, run.run_id)
    assert "report.ready" in types
    assert "run.failed" in types

    artifact = await ArtifactRepository(conn).get(updated.report_artifact_id)
    assert artifact is not None
    assert artifact.original_storage_path


@pytest.mark.asyncio
async def test_executor_two_graph_attempts_then_structured_finalization_succeeds(env):
    """两轮普通 graph attempt 后 structured finalization 成功：run SUCCEEDED。"""
    settings, conn, session, run, publisher, executor = env
    batches = [
        [_model_end("第一轮文本")],
        [_model_end("第二轮文本")],
    ]
    stub = _SequenceStubAgent(batches)
    model = _StructuredModel(
        {
            "title": "final",
            "sections": [],
            "recommendations": [],
            "disagreements": [],
            "unknowns": [],
        },
        extra_body={"trace_id": "keep", "thinking": {"type": "enabled"}},
        reject_thinking_tool_choice=True,
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.report_artifact_id is not None
    assert stub._calls == 2
    assert len(model.calls) == 1
    assert model.calls[0]["schema"].__name__ == "ResearchReport"
    assert model.calls[0]["method"] == "function_calling"
    assert model.calls[0]["extra_body"] == {
        "trace_id": "keep",
        "thinking": {"type": "disabled"},
    }
    assert model.extra_body == {
        "trace_id": "keep",
        "thinking": {"type": "enabled"},
    }


@pytest.mark.asyncio
async def test_executor_budget_on_second_attempt_stops_before_third(env):
    """第二次 attempt 探索预算触顶：停止后续 graph，进入 structured finalization。"""
    settings, conn, session, run, publisher, executor = env
    executor._settings.agent_max_tool_calls = 1
    batches = [
        [_model_end("第一轮文本")],
        [_tool_start("search_web", "r2", {"query": "budget"})],
    ]
    stub = _SequenceStubAgent(batches)
    model = _StructuredModel(
        {
            "title": "structured final",
            "sections": [],
            "recommendations": [],
            "disagreements": [],
            "unknowns": [],
        }
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.error_code is None
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
    """探索软预算触顶后停止当前 graph，进入 structured finalization。"""
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
    model = _StructuredModel(
        {
            "title": "structured final",
            "sections": [],
            "recommendations": [],
            "disagreements": [],
            "unknowns": [],
        }
    )

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(model),
        ),
    ):
        await executor(run2.run_id)

    updated = await RunRepository(conn).get(run2.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.error_code is None
    assert updated.report_artifact_id is not None
    assert stub._calls == 1

    artifacts = await ArtifactRepository(conn).list_for_session(session.session_id)
    assert len(artifacts) == 1

    db_events = await EventRepository(conn).list_after(run2.run_id, 0)
    assert sum(event.type == "report.ready" for event in db_events) == 1
    assert sum(event.type == "run.succeeded" for event in db_events) == 1
    assert sum(event.type == "run.failed" for event in db_events) == 0
    assert not any(
        event.type == "tool.completed"
        and event.payload.get("tool_name") == "search_web"
        and event.payload.get("tool_call_id") == "sX"
        for event in db_events
    )
    assert not any(
        event.type == "report.ready" and event.payload.get("degraded")
        for event in db_events
    )


@pytest.mark.asyncio
async def test_executor_exploration_budget_structured_failure_is_run_failed(env):
    """探索预算软拦截后的 structured 失败必须合并原因并归类 RUN_FAILED。"""
    settings, conn, session, run, publisher, executor = env
    run2 = Run(
        session_id=session.session_id,
        request=ResearchRequest(
            question="测试问题：探索预算触顶后 structured 失败",
            constraints=["max_tool_calls=6"],
        ),
    )
    await RunRepository(conn).create(run2)
    events = [
        *_explore_events(4),
        _tool_start("extract_web_sources", "sX", {"query": "over budget"}),
    ]
    stub = _SequenceStubAgent([events])

    with (
        patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub),
        patch(
            "ai_dev_researcher.services.agent_executor.create_model_binding",
            return_value=_ModelBinding(_FailingStructuredModel()),
        ),
    ):
        await executor(run2.run_id)

    updated = await RunRepository(conn).get(run2.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "RUN_FAILED"
    assert "exploration_budget" in (updated.error_message or "")
    assert "structured finalization failed" in (updated.error_message or "")
    assert updated.report_artifact_id is not None
    assert stub._calls == 1

    artifacts = await ArtifactRepository(conn).list_for_session(session.session_id)
    assert len(artifacts) == 1
    db_events = await EventRepository(conn).list_after(run2.run_id, 0)
    assert sum(event.type == "report.ready" for event in db_events) == 1
    assert sum(event.type == "run.failed" for event in db_events) == 1
    assert sum(event.type == "run.succeeded" for event in db_events) == 0
    assert not any(
        event.type == "tool.completed"
        and event.payload.get("tool_name") == "extract_web_sources"
        and event.payload.get("tool_call_id") == "sX"
        for event in db_events
    )


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
async def test_executor_stage_budget_report_starts_after_ledger_completion(env):
    """ledger 完成后进入 report，看门狗应覆盖后续模型组织静默期。"""
    settings, conn, session, run, publisher, executor = env
    executor._settings.agent_max_elapsed_seconds = 0
    executor._settings.agent_plan_timeout_seconds = 0
    executor._settings.agent_research_timeout_seconds = 1.0
    executor._settings.agent_report_timeout_seconds = 0.05
    executor._settings.agent_idle_timeout_seconds = 0
    stub = _LedgerThenDelayedEventAgent(delay=0.08)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.FAILED
    assert updated.error_code == "BUDGET_EXCEEDED"
    assert "stage_timeout:report" in (updated.error_message or "")
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
