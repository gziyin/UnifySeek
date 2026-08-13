"""submit_research_report 工具的参数 schema。

langchain StructuredTool 会将其扁平化为顶层字段（title/sections/...），
嵌套模型（ReportSection/ResearchClaim）保留为 $defs 引用，模型按字段名直调。
字段 description 是模型可见的指引，直接影响首次提交成功率。

调用链：model -> submit_research_report(...) -> submit_research_report_impl
（impl 内部仍做 ResearchReport.model_validate 严格校验）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ai_dev_researcher.domain.reports import (
    Disagreement,
    ResearchClaim,
    ReportSection,
)


class SubmitResearchReportArgs(BaseModel):
    """最终研究报告。调用前必须先 search_web 收集证据并核对 get_evidence_ledger。"""

    title: str = Field(
        description="报告标题，概括本次研究问题（如 'DeepAgents 与 LangGraph 编排方式对比'）"
    )
    executive_summary_claim_ids: list[str] = Field(
        min_length=1,
        description="执行摘要引用的 claim ID 列表。这些 ID 必须是本参数中 claims（sections/recommendations 内）真实存在的 id。",
    )
    sections: list[ReportSection] = Field(
        description="报告正文分节。每节 heading + claims（可为空，支持仅子节/仅表格），可递归 subsections（多级编号标题，如 一、/1.），可含可选 table 对比表；statement 写成完整句子/段落、可用 **粗体** 强调；每个 claim 的 citation_ids 必须是证据 ledger 中真实存在的证据 ID（形如 S1、S2、D1）。引用统一用 citation_ids，渲染层自动转 [n] 编号，不要手写编号。"
    )
    disagreements: list[Disagreement] = Field(
        default_factory=list,
        description="可选：不同来源对同一问题存在矛盾证据时，记录冲突主题与双方立场（sides 至少 2 个，每个立场带 citation_ids）。",
    )
    unknowns: list[str] = Field(
        default_factory=list,
        description="可选：无法验证的信息或已知空白，逐条用字符串描述。",
    )
    recommendations: list[ResearchClaim] = Field(
        min_length=1,
        description="结论与建议。每个 claim 写成完整句子/段落，可用 **粗体** 强调关键结论；同样必须带 citation_ids，引用真实证据 ID（渲染层自动转 [n]）。",
    )
