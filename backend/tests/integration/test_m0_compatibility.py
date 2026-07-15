from __future__ import annotations

import inspect
import os

import pytest

from ai_dev_researcher.agents.model import create_model_binding
from ai_dev_researcher.agents.orchestrator import create_research_agent
from ai_dev_researcher.agents.profiles import register_project_profile
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.storage.paths import WorkspacePaths
from uuid import uuid4

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore


pytestmark = pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY required for M0 compatibility spike",
)


@pytest.mark.asyncio
async def test_deepagents_import_and_profile_registration(tmp_path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    binding = create_model_binding(settings)
    register_project_profile(binding.spec)
    assert binding.spec.startswith("deepseek:")


@pytest.mark.asyncio
async def test_create_research_agent_has_only_custom_subagents(tmp_path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    binding = create_model_binding(settings)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="测试 DeepAgents 子智能体配置是否符合项目约束",
        uploaded_artifact_ids=[],
        max_web_sources=5,
        paths=paths,
        settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    agent = create_research_agent(
        context,
        binding,
        store,
        ArtifactRepository(conn),
    )
    assert hasattr(agent, "astream_events")
    sig = inspect.signature(agent.astream_events)
    assert "version" in sig.parameters

    # Ensure graph compiles and exposes task tool via nodes.
    graph = agent.get_graph()
    node_names = " ".join(graph.nodes.keys()).lower()
    assert "task" in node_names or "tools" in node_names
    assert "general-purpose" not in node_names
    await conn.close()


@pytest.mark.asyncio
async def test_astream_events_v3_emits_events(tmp_path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
        fake_agent_mode=False,
    )
    binding = create_model_binding(settings)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="请只调用 write_todos 制定一个两步研究计划，不要委派子智能体，不要提交报告。",
        uploaded_artifact_ids=[],
        max_web_sources=3,
        paths=paths,
        settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    agent = create_research_agent(
        context,
        binding,
        store,
        ArtifactRepository(conn),
    )
    stream = agent.astream_events(
        {"messages": [{"role": "user", "content": context.question}]},
        config={"configurable": {"thread_id": str(run_id)}},
        version="v3",
    )
    if inspect.isawaitable(stream):
        stream = await stream

    seen: list[str] = []
    async for raw in stream:
        if isinstance(raw, dict) and raw.get("event"):
            seen.append(str(raw["event"]))
        if len(seen) >= 3:
            break
    assert seen
    await conn.close()
