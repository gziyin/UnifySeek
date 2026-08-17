from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from ai_dev_researcher.agents.context import RunContext
from ai_dev_researcher.agents.prompts import (
    DOCUMENT_ANALYST_PROMPT,
    build_orchestrator_prompt,
)
from ai_dev_researcher.core.config import Settings
from ai_dev_researcher.storage.paths import WorkspacePaths


def _context(tmp_path: Path) -> RunContext:
    settings = Settings(workspace_root=tmp_path / "ws", fake_agent_mode=True)
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


def test_orchestrator_prompt_mandates_kb_record_when_prefetch_relevant(tmp_path: Path):
    """#44：预检相关时 orchestrator 必须委托 document-analyst 并落 K 证据（硬约束）。"""
    prompt = build_orchestrator_prompt(_context(tmp_path))
    # 升级措辞：可委托 → 必须委托。
    assert "必须委托 document-analyst" in prompt
    # record 路径与其承载 K 证据的语义都被点名。
    assert "record_knowledge_base_evidence" in prompt
    assert "K 类证据 ID" in prompt
    assert "禁止跳过委托" in prompt
    # 预检片段仅供判断相关性与 K citation 纪律：不得把未落账本内容当引用来源。
    assert "未写入证据账本" in prompt
    assert "不得作为 citation 来源" in prompt
    assert "伪装成 S 类网络来源引用" in prompt
    # 预算耗尽兜底：知识库结论记入 unknowns，正只引已入账本证据。
    assert "unknowns" in prompt
    # 回归保真（#13 句柄迎面）：
    assert "判断研究问题与本地知识库主题是否相关" in prompt
    assert "跳过 document-analyst 的知识库分支" in prompt
    assert "search_knowledge_base" in prompt
    assert "read_knowledge_base_file" in prompt
    assert "未调用 search_web 前，禁止调用 submit_research_report" in prompt


def test_document_analyst_prompt_mandates_record_on_high_score():
    """#44：document-analyst 对高分命中必须先 record（硬约束），并带配额经济性指引。"""
    prompt = DOCUMENT_ANALYST_PROMPT
    # 硬约束：高分命中后必须先 record，禁止只读不记。
    assert "必须先" in prompt
    assert "record_knowledge_base_evidence" in prompt
    assert "硬约束" in prompt
    assert "禁止只读不记" in prompt
    # 配额经济性：窄窗口 read 防 search/read 吃光预算饿死 record。
    assert "窄窗口" in prompt
    assert "line_start/line_end" in prompt
    assert "共享" in prompt
    # 回归保真（#13）：
    assert "search_knowledge_base" in prompt
    assert "read_knowledge_base_file" in prompt
    assert "list_knowledge_base_entries" in prompt
    assert "不要浏览知识库" in prompt
    assert "直接浏览" not in prompt