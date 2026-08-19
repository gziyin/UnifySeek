from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.agents.prompts import DOCUMENT_ANALYST_PROMPT, build_orchestrator_prompt
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
from ai_dev_researcher.tools.knowledge_base import (
    KB_BUDGET_EXHAUSTED_GUIDANCE,
    KbToolBudget,
    search_knowledge_base_impl,
)

ARTIFACT_ID = "851a4589-edee-470e-9732-0ee5548fa5b7"


def _tool_start(name: str, run_id: str, input_: dict) -> dict:
    return {"event": "on_tool_start", "name": name, "run_id": run_id, "data": {"input": input_}}


def _tool_end(name: str, run_id: str, output) -> dict:
    return {"event": "on_tool_end", "name": name, "run_id": run_id, "data": {"output": output}}


class _StubAgent:
    def __init__(self, events: list[dict]):
        self._events = events

    def astream_events(self, *args, **kwargs):  # noqa: ANN002, ANN003
        async def _gen():
            for ev in self._events:
                yield ev

        return _gen()


class _RecordingIndex:
    """Records every retrieve() call (query/threshold) and simulates score filtering."""

    is_ready = True

    def __init__(self, chunks: list[dict] | None = None):
        self._chunks = chunks or []
        self.calls: list[dict] = []

    def retrieve(self, query: str, path=None, top_k: int = 10, score_threshold: float = 0.0):  # noqa: ANN001, ANN002, ANN003
        self.calls.append(
            {
                "query": query,
                "path": path,
                "top_k": top_k,
                "score_threshold": score_threshold,
            }
        )
        return [
            KbChunk(**item)
            for item in self._chunks
            if (item.get("score") or 0.0) >= score_threshold
        ]


def _kb_tools(settings: Settings, index=None, budget: KbToolBudget | None = None):
    paths = WorkspacePaths(settings.sessions_root)
    context = RunContext(
        run_id=uuid4(),
        session_id=uuid4(),
        question="q",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    tools = create_document_tools(
        context,
        store=object(),
        artifacts=object(),
        knowledge_index=index,
        kb_budget=budget if budget is not None else KbToolBudget(settings.kb_max_tool_calls),
    )
    return {t.name: t for t in tools}, context


def _context(settings: Settings) -> RunContext:
    paths = WorkspacePaths(settings.sessions_root)
    return RunContext(
        run_id=uuid4(),
        session_id=uuid4(),
        question="q",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# 闸门 1：提示词决策层
# ---------------------------------------------------------------------------


def test_orchestrator_prompt_gates_kb_by_relevance(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    prompt = build_orchestrator_prompt(_context(settings))
    assert "判断研究问题与本地知识库主题是否相关" in prompt
    assert "跳过 document-analyst 的知识库分支" in prompt
    assert "document-analyst" in prompt
    assert "委托" in prompt
    assert "search_knowledge_base" in prompt
    assert "read_knowledge_base_file" in prompt
    # 硬规则保持：未调用 search_web 前禁止 submit。
    assert "未调用 search_web 前，禁止调用 submit_research_report" in prompt


def test_document_analyst_prompt_constrains_kb_browsing():
    assert "search_knowledge_base" in DOCUMENT_ANALYST_PROMPT
    assert "read_knowledge_base_file" in DOCUMENT_ANALYST_PROMPT
    assert "先" in DOCUMENT_ANALYST_PROMPT
    assert "语义定位" in DOCUMENT_ANALYST_PROMPT
    # 新约束：预检相关或高分命中才读，无关不浏览。
    assert "不要浏览知识库" in DOCUMENT_ANALYST_PROMPT
    assert "list_knowledge_base_entries" in DOCUMENT_ANALYST_PROMPT
    # 弱化「直接浏览」回退诱导。
    assert "直接浏览" not in DOCUMENT_ANALYST_PROMPT


# ---------------------------------------------------------------------------
# 闸门 2：工具层默认阈值
# ---------------------------------------------------------------------------


async def test_search_tool_default_threshold_uses_settings(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    settings.kb_prefetch_score_threshold = 0.3
    index = _RecordingIndex()
    tools, _ = _kb_tools(settings, index)
    await tools["search_knowledge_base"].ainvoke({"query": "q"})
    assert index.calls[0]["score_threshold"] == 0.3


async def test_search_tool_clamps_explicit_zero(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    settings.kb_prefetch_score_threshold = 0.3
    index = _RecordingIndex()
    tools, _ = _kb_tools(settings, index)
    await tools["search_knowledge_base"].ainvoke({"query": "q", "score_threshold": 0.0})
    assert index.calls[0]["score_threshold"] == 0.3


async def test_search_tool_respects_higher_threshold(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    settings.kb_prefetch_score_threshold = 0.3
    index = _RecordingIndex()
    tools, _ = _kb_tools(settings, index)
    await tools["search_knowledge_base"].ainvoke({"query": "q", "score_threshold": 0.7})
    assert index.calls[0]["score_threshold"] == 0.7


async def test_search_tool_filters_low_score_chunks(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    settings.kb_prefetch_score_threshold = 0.3
    index = _RecordingIndex(
        [
            {
                "file_path": "low.md",
                "symbol": "low",
                "parent_symbol": "",
                "kind": "doc",
                "line_start": 1,
                "line_end": 2,
                "score": 0.1,
                "text": "irrelevant",
            },
            {
                "file_path": "high.md",
                "symbol": "high",
                "parent_symbol": "",
                "kind": "doc",
                "line_start": 1,
                "line_end": 2,
                "score": 0.9,
                "text": "relevant",
            },
        ]
    )
    tools, _ = _kb_tools(settings, index)
    result = await tools["search_knowledge_base"].ainvoke({"query": "q"})
    paths = [item["file_path"] for item in result["results"]]
    assert paths == ["high.md"]


# ---------------------------------------------------------------------------
# 闸门 3：KB 软预算（工厂层）
# ---------------------------------------------------------------------------


async def test_kb_tool_budget_short_circuits_search(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    settings.kb_max_tool_calls = 2
    index = _RecordingIndex()
    tools, _ = _kb_tools(settings, index)
    for _ in range(2):
        await tools["search_knowledge_base"].ainvoke({"query": "q"})
    blocked = await tools["search_knowledge_base"].ainvoke({"query": "q"})
    assert len(index.calls) == 2
    assert blocked["note"] == "budget_exceeded"
    assert "KB 软预算已用尽" in blocked["guidance"]


async def test_kb_tool_budget_covers_all_kb_tools(tmp_path: Path):
    kb_root = tmp_path / "kb"
    kb_root.mkdir(parents=True)
    (kb_root / "notes.md").write_text("# Notes\n\ncontent\n", encoding="utf-8")
    settings = Settings(
        workspace_root=tmp_path / "ws",
        knowledge_base_root=kb_root,
        fake_agent_mode=True,
    )
    settings.kb_max_tool_calls = 1
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    paths = WorkspacePaths(settings.sessions_root, knowledge_base_root=kb_root)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="q",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    index = _RecordingIndex(
        [
            {
                "file_path": "notes.md",
                "symbol": "notes",
                "parent_symbol": "",
                "kind": "doc",
                "line_start": 1,
                "line_end": 3,
                "score": 0.9,
                "text": "content",
            }
        ]
    )
    tools = {
        t.name: t
        for t in create_document_tools(
            context,
            store=store,
            artifacts=object(),
            knowledge_index=index,
            kb_budget=KbToolBudget(settings.kb_max_tool_calls, k_evidence_limit=5),
        )
    }

    # 先 search：命中候选并注册（消耗唯一一次 search 预算）。
    found = await tools["search_knowledge_base"].ainvoke({"query": "notes"})
    assert found["count"] == 1

    # 预算已耗尽：list/read 被短路。
    read = await tools["read_knowledge_base_file"].ainvoke({"path": "notes.md"})
    assert read["note"] == "budget_exceeded"
    assert read["text"] == ""

    # #44：record 豁免 KB 软预算 —— 即使 search/read/list 已耗尽预算，绑定候选后仍成功落账本。
    record = await tools["record_knowledge_base_evidence"].ainvoke(
        {
            "path": "notes.md",
            "title": "t",
            "excerpt": "e",
            "line_start": 1,
            "line_end": 2,
        }
    )
    assert "budget_exceeded" not in (record.get("note") or "")
    assert record["evidence_id"].startswith("K")
    ledger = await store.list_for_run()
    assert any(item.source_type == "knowledge_base" for item in ledger)

    # 重复候选幂等：同候选再次 record 返回同一 evidence_id（note=duplicate），账本不增。
    duplicate = await tools["record_knowledge_base_evidence"].ainvoke(
        {
            "path": "notes.md",
            "title": "t",
            "excerpt": "e",
            "line_start": 1,
            "line_end": 2,
        }
    )
    assert duplicate["note"] == "duplicate"
    assert duplicate["evidence_id"] == record["evidence_id"]
    assert len(await store.list_for_run()) == 1

    listing = await tools["list_knowledge_base_entries"].ainvoke({"path": "."})
    assert listing["note"] == "budget_exceeded"
    assert listing["entries"] == []
    await conn.close()


async def test_kb_tool_budget_zero_means_unlimited(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
    settings.kb_max_tool_calls = 0
    index = _RecordingIndex()
    tools, _ = _kb_tools(settings, index)
    for _ in range(20):
        await tools["search_knowledge_base"].ainvoke({"query": "q"})
    assert len(index.calls) == 20


# ---------------------------------------------------------------------------
# 闸门 3：KB 软预算（executor 事件层）
# ---------------------------------------------------------------------------


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
        request=ResearchRequest(question="测试问题：验证 KB 相关性闸门"),
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


async def test_executor_kb_budget_blocked_run_succeeds_with_guidance(env):
    """被短路的 KB 工具返回空结果 + 引导说明，run 照常 SUCCEEDED（不 FAILED/BUDGET_EXCEEDED）。"""
    settings, conn, session, run, publisher, executor = env
    executor._settings.kb_max_tool_calls = 1
    events = [
        _tool_start("search_knowledge_base", "r1", {"query": "q"}),
        _tool_end("search_knowledge_base", "r1", {"results": [], "count": 0}),
        _tool_start("search_knowledge_base", "r2", {"query": "q2"}),
        _tool_end(
            "search_knowledge_base",
            "r2",
            {
                "results": [],
                "count": 0,
                "note": "budget_exceeded",
                "guidance": KB_BUDGET_EXHAUSTED_GUIDANCE,
            },
        ),
        _tool_start("submit_research_report", "r3", {"title": "t"}),
        _tool_end("submit_research_report", "r3", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    with patch("ai_dev_researcher.services.agent_executor.create_research_agent", return_value=stub):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert "run.failed" not in await _event_types(conn, run.run_id)

    # 被短路的 KB 工具说明随 tool.completed 的 output_summary 发布。
    completed = [
        e
        for e in await EventRepository(conn).list_after(run.run_id, 0)
        if e.type == "tool.completed"
    ]
    assert any("budget_exceeded" in (e.payload.get("output_summary") or "") for e in completed)


async def test_executor_record_still_writes_k_after_budget_exhausted(env):
    """#44：record 豁免 KB 软预算 —— 即使 search 已因预算耗尽被短路，
    record_knowledge_base_evidence 仍成功落账本并发布 K 账本事件。"""
    settings, conn, session, run, publisher, executor = env
    events = [
        _tool_start("search_knowledge_base", "r1", {"query": "q"}),
        _tool_end(
            "search_knowledge_base",
            "r1",
            {
                "results": [],
                "count": 0,
                "note": "budget_exceeded",
                "guidance": KB_BUDGET_EXHAUSTED_GUIDANCE,
            },
        ),
        _tool_start("record_knowledge_base_evidence", "r2", {"path": "notes.md"}),
        _tool_end(
            "record_knowledge_base_evidence",
            "r2",
            {
                "evidence_id": "K1",
                "locator": "kb:notes.md lines 1-2",
                "path": "notes.md",
                "line_start": 1,
                "line_end": 2,
                "excerpt": "e",
                "title": "t",
            },
        ),
        _tool_start("submit_research_report", "r3", {"title": "t"}),
        _tool_end("submit_research_report", "r3", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        return_value=stub,
    ):
        await executor(run.run_id)

    updated = await RunRepository(conn).get(run.run_id)
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED

    db_events = await EventRepository(conn).list_after(run.run_id, 0)
    discovered = [
        e
        for e in db_events
        if e.type == "source.discovered" and e.payload.get("source_type") == "knowledge_base"
    ]
    assert [e.payload.get("evidence_id") for e in discovered] == ["K1"]
    recorded = [
        e
        for e in db_events
        if e.type == "evidence.recorded" and e.payload.get("source_type") == "knowledge_base"
    ]
    assert [e.payload.get("evidence_id") for e in recorded] == ["K1"]


async def test_executor_kb_budget_wired_via_di(env):
    """预算对象由 executor 创建并经 DI 注入 create_research_agent（run 级，取 output_mode profile）。"""
    settings, conn, session, run, publisher, executor = env
    run2 = Run(
        session_id=session.session_id,
        request=ResearchRequest(question="short mode kb budget", output_mode="short"),
    )
    await RunRepository(conn).create(run2)
    captured = {}
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end(
            "search_web",
            "r1",
            {"items": [{"evidence_id": "S1", "title": "DeepAgents", "url": "https://x"}]},
        ),
        _tool_start("submit_research_report", "r2", {"title": "t"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None, kb_budget=None):
        captured["kb_budget"] = kb_budget
        return stub

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        side_effect=_fake_create,
    ):
        await executor(run2.run_id)

    budget = captured["kb_budget"]
    assert isinstance(budget, KbToolBudget)
    # short 模式 profile 初始值：KB 6 + K 证据上限 3。
    assert budget.limit == 6
    assert budget.remaining == 6
    assert budget.k_evidence_limit == 3


async def test_executor_k_evidence_wired_from_medium_profile(env):
    """默认 medium 模式：K 证据上限 5 随 KbToolBudget 注入。"""
    settings, conn, session, run, publisher, executor = env
    captured = {}
    events = [
        _tool_start("submit_research_report", "r2", {"title": "t"}),
        _tool_end("submit_research_report", "r2", {"artifact_id": ARTIFACT_ID, "title": "t"}),
    ]
    stub = _StubAgent(events)

    def _fake_create(context, model_binding, store, artifacts, vector_store=None, knowledge_index=None, kb_budget=None):
        captured["kb_budget"] = kb_budget
        return stub

    with patch(
        "ai_dev_researcher.services.agent_executor.create_research_agent",
        side_effect=_fake_create,
    ):
        await executor(run.run_id)

    budget = captured["kb_budget"]
    assert budget.k_evidence_limit == 5


async def test_executor_kb_prefetch_empty_skips_context_and_evidence(env, tmp_path: Path):
    """预取结果为空：不注入 KB 上下文、账本无 K 证据（无关问题的确定性闸门）。"""
    settings, conn, session, run, publisher, executor = env
    kb_root = tmp_path / "kb"
    kb_root.mkdir(parents=True)
    settings.knowledge_base_root = kb_root
    executor._knowledge_index = _RecordingIndex([])
    captured = {}
    events = [
        _tool_start("search_web", "r1", {"query": "DeepAgents"}),
        _tool_end(
            "search_web",
            "r1",
            {"items": [{"evidence_id": "S1", "title": "DeepAgents", "url": "https://x"}]},
        ),
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
    assert captured["context"].knowledge_context == ""

    store = EvidenceStore(
        run_id=run.run_id,
        session_id=run.session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths_for_test(settings),
    )
    ledger = await store.list_for_run()
    assert not any(item.source_type == "knowledge_base" for item in ledger)


def test_settings_kb_max_tool_calls(monkeypatch, tmp_path: Path):
    assert Settings(workspace_root=tmp_path / "ws").kb_max_tool_calls == 12
    assert Settings(workspace_root=tmp_path / "ws", kb_max_tool_calls=3).kb_max_tool_calls == 3
    monkeypatch.setenv("KB_MAX_TOOL_CALLS", "7")
    assert Settings(workspace_root=tmp_path / "ws").kb_max_tool_calls == 7


# ---------------------------------------------------------------------------
# 闸门 4：候选注册 run-scoped（预取不授权 record）+ 重复候选不重复发布账本事件
# ---------------------------------------------------------------------------


async def test_only_factory_search_registers_candidate_for_record(tmp_path: Path):
    """预取（direct impl）不注册候选：record 被拒；工厂 search 工具注册候选后 record 成功。"""
    kb_root = tmp_path / "kb"
    kb_root.mkdir(parents=True)
    (kb_root / "notes.md").write_text("# Notes\n\ncontent\n", encoding="utf-8")
    settings = Settings(
        workspace_root=tmp_path / "ws",
        knowledge_base_root=kb_root,
        fake_agent_mode=True,
    )
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    paths = WorkspacePaths(settings.sessions_root, knowledge_base_root=kb_root)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="q",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    index = _RecordingIndex(
        [
            {
                "file_path": "notes.md",
                "symbol": "notes",
                "parent_symbol": "",
                "kind": "doc",
                "line_start": 1,
                "line_end": 3,
                "score": 0.9,
                "text": "content",
            }
        ]
    )
    guard = KbToolBudget(limit=10, k_evidence_limit=0)
    tools = {
        t.name: t
        for t in create_document_tools(
            context,
            store=store,
            artifacts=object(),
            knowledge_index=index,
            kb_budget=guard,
        )
    }

    async def _record() -> dict:
        return await tools["record_knowledge_base_evidence"].ainvoke(
            {
                "path": "notes.md",
                "title": "t",
                "excerpt": "e",
                "line_start": 1,
                "line_end": 2,
            }
        )

    # 1) 预取路径：executor 直调 impl（不经工厂工具），不注册候选。
    await search_knowledge_base_impl(query="notes", knowledge_index=index)
    blocked = await _record()
    assert blocked["note"] == "candidate_rejected"
    assert await store.list_for_run() == []

    # 2) 工厂 search 工具：注册候选（路径+行号重叠+阈值）。
    found = await tools["search_knowledge_base"].ainvoke({"query": "notes"})
    assert found["count"] == 1
    ok = await _record()
    assert ok["evidence_id"].startswith("K")
    assert "candidate_rejected" not in (ok.get("note") or "")
    assert len(await store.list_for_run()) == 1
    await conn.close()


async def test_executor_duplicate_record_does_not_publish_dup_ledger_events(env):
    """重复候选记录（note=duplicate）不发布 source.discovered / evidence.recorded 账本事件。"""
    settings, conn, session, run, publisher, executor = env
    events = [
        _tool_start("record_knowledge_base_evidence", "r1", {"path": "notes.md"}),
        _tool_end(
            "record_knowledge_base_evidence",
            "r1",
            {
                "evidence_id": "K1",
                "locator": "kb:notes.md lines 1-2",
                "path": "notes.md",
                "line_start": 1,
                "line_end": 2,
                "excerpt": "e",
                "note": "duplicate",
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
    discovered = [
        e
        for e in db_events
        if e.type == "source.discovered" and e.payload.get("source_type") == "knowledge_base"
    ]
    recorded_events = [
        e
        for e in db_events
        if e.type == "evidence.recorded" and e.payload.get("source_type") == "knowledge_base"
    ]
    assert discovered == []
    assert recorded_events == []


async def _event_types(conn, run_id) -> list[str]:
    events = await EventRepository(conn).list_after(run_id, 0)
    return [e.type for e in events]


def paths_for_test(settings: Settings) -> WorkspacePaths:
    return WorkspacePaths(settings.sessions_root)
