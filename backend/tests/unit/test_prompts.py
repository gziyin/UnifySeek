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
from ai_dev_researcher.domain.runs import DEFAULT_OUTPUT_MODE, OutputMode
from ai_dev_researcher.storage.paths import WorkspacePaths


def _context(tmp_path: Path, output_mode: OutputMode = DEFAULT_OUTPUT_MODE) -> RunContext:
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
        output_mode=output_mode,
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


def test_orchestrator_prompt_renders_output_mode_soft_targets(tmp_path: Path):
    """output_mode 注入 prompt：模式、软目标数值、优先级与预算收束指引都在。"""
    prompt = build_orchestrator_prompt(_context(tmp_path, OutputMode.SHORT))
    assert "调研输出模式：short" in prompt
    # 软目标（非硬性上限）数值来自 short profile：120s / 24 次 / 6 次 KB。
    assert "120" in prompt
    assert "24 次" in prompt
    assert "6 次" in prompt
    # short 内容软目标：1200-1800 字 / 2-4 章 / 2-3 条建议；三档统一 2-4 条核心结论。
    assert "1200-1800" in prompt
    assert "2-4 个章节" in prompt
    assert "2-3 条行动建议" in prompt
    assert "三档统一 2-4 条" in prompt
    # 软性措辞 + 质量优先级 + 预算收束 + 不机械截断。
    assert "软目标" in prompt
    assert "结论完整性与证据引用" in prompt
    assert "unknowns" in prompt
    assert "不要继续搜索或发起重试" in prompt
    assert "不做机械式截断" in prompt
    assert "硬性字数/章节上限" in prompt
    # 所有模式都作用于网页、上传文档与知识库深度。
    assert "均覆盖网页调研、上传文档与本地知识库三类来源" in prompt


def test_orchestrator_prompt_mode_profile_variants(tmp_path: Path):
    """medium/long 分别注入对应 profile 的软目标数值与内容篇幅目标。"""
    medium_prompt = build_orchestrator_prompt(
        _context(tmp_path, OutputMode.MEDIUM)
    )
    long_prompt = build_orchestrator_prompt(_context(tmp_path, OutputMode.LONG))
    assert "调研输出模式：medium" in medium_prompt
    assert "40 次" in medium_prompt
    assert "2500-4000" in medium_prompt
    assert "3-6 个章节" in medium_prompt
    assert "2-5 条行动建议" in medium_prompt
    assert "调研输出模式：long" in long_prompt
    assert "60 次" in long_prompt
    assert "5000-8000" in long_prompt
    assert "5-8 个章节" in long_prompt
    assert "3-7 条行动建议" in long_prompt
    # 三档统一：核心结论始终 2-4 条（短中长一致）。
    assert "三档统一 2-4 条" in medium_prompt
    assert "三档统一 2-4 条" in long_prompt


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