from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from langchain_core.tools import StructuredTool

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.evidence_store import EvidenceStore
from ai_dev_researcher.storage.paths import WorkspacePaths
from ai_dev_researcher.tools.factory import create_orchestrator_tools


@pytest.fixture
async def env(tmp_path: Path):
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key="test-key",
    )
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="测试问题：对比两个框架的编排差异以验证报告提交",
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
    yield context, store, ArtifactRepository(conn)
    await conn.close()


async def _submit_tool(env) -> StructuredTool:
    context, store, artifacts = env
    tools = create_orchestrator_tools(context, store, artifacts)
    return next(t for t in tools if t.name == "submit_research_report")


_VALID_PAYLOAD = {
    "title": "DeepAgents 与 LangGraph 编排对比",
    "executive_summary_claim_ids": ["C1"],
    "sections": [
        {
            "heading": "子智能体委派",
            "claims": [
                {
                    "id": "C1",
                    "statement": "DeepAgents 通过 task 工具委派",
                    "citation_ids": ["S1"],
                    "confidence": "medium",
                }
            ],
        }
    ],
    "recommendations": [
        {"id": "R1", "statement": "个人项目建议使用 DeepAgents", "citation_ids": ["S1"], "confidence": "low"}
    ],
    "unknowns": ["两者性能基准数据暂缺"],
}


@pytest.mark.asyncio
async def test_submit_tool_args_schema_exposes_structured_fields(env):
    tool = await _submit_tool(env)
    # StructuredTool 将 args_schema 扁平化为顶层字段（无 "properties" 包装）
    props = tool.args
    for field in ["title", "executive_summary_claim_ids", "sections", "recommendations", "disagreements", "unknowns"]:
        assert field in props, f"missing schema field: {field}"
    assert props["sections"].get("description")
    assert props["recommendations"].get("minItems") == 1


@pytest.mark.asyncio
async def test_submit_tool_forwards_structured_report_data(env):
    context, store, artifacts = env
    tool = await _submit_tool(env)

    captured: dict = {}

    async def _fake_impl(**kwargs) -> dict:
        captured.update(kwargs)
        return {"artifact_id": "851a4589-edee-470e-9732-0ee5548fa5b7", "title": "t"}

    with patch("ai_dev_researcher.tools.factory.submit_research_report_impl", new=_fake_impl):
        result = await tool.ainvoke(_VALID_PAYLOAD)

    assert result["artifact_id"]
    assert captured["report_data"]["title"] == "DeepAgents 与 LangGraph 编排对比"
    section = captured["report_data"]["sections"][0]
    assert section["heading"] == "子智能体委派"
    assert section["claims"][0]["citation_ids"] == ["S1"]
    assert captured["report_data"]["unknowns"] == ["两者性能基准数据暂缺"]
    # 嵌套模型已转纯 JSON dict，可被 ResearchReport.model_validate 直接消费
    from ai_dev_researcher.domain.reports import ResearchReport

    ResearchReport.model_validate(captured["report_data"])


@pytest.mark.asyncio
async def test_submit_tool_rejects_invalid_payload(env):
    tool = await _submit_tool(env)
    bad = {**_VALID_PAYLOAD, "recommendations": []}
    with pytest.raises(Exception):
        await tool.ainvoke(bad)
