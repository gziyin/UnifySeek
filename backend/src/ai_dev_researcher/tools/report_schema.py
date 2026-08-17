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
    summary_claims: list[ResearchClaim] = Field(
        default_factory=list,
        description="(推荐) \u6700\u540e\u57fa\u4e8e\u5168\u6587\u4e0e\u8bc1\u636e\u8d26\u672c\u84b8\u998f\u7684 2~4 \u6761\u5168\u65b0\u6838\u5fc3\u7ed3\u8bba\uff1b\u6bcf\u6761\u5fc5\u987b\u662f\u5b8c\u6574\u81ea\u6d3d\u7684\u53e5\u5b50\uff08\u53ef\u542b **\u7c97\u4f53** \u5f3a\u8c03\u5173\u952e\u70b9\uff09\u3001\u2264120 \u5b57\u3001\u7efc\u5408\u6027\u8868\u8ff0\u3001\u5f15\u7528\u6700\u5177\u652f\u6491\u529b\u7684\u8bc1\u636e\u7f16\u53f7\u3001\u7981\u6b62\u7167\u6284\u6216\u6539\u5199\u6b63\u6587\u53e5\u5b50\u3001\u7981\u6b62\u4f7f\u7528\u7701\u7565\u53f7\u300e\u2026\u300f\u3001\u7981\u6b62\u8f93\u51fa\u4e0d\u5b8c\u6574\u6216\u88ab\u622a\u65ad\u7684\u534a\u53e5\u3002\u6838\u5fc3\u6c47\u603b\u6e32\u67d3\u4ee5\u6b64\u4f18\u5148\u3002",
    )
    executive_summary_claim_ids: list[str] = Field(
        default_factory=list,
        description="(\u65e7\u683c\u5f0f\u517c\u5bb9) \u6267\u884c\u6458\u8981\u5f15\u7528\u7684 claim ID \u5217\u8868\uff0c\u4ec5\u5728\u672a\u63d0\u4f9b summary_claims \u65f6\u56de\u9000\u751f\u6548\uff1b\u4e24\u8005\u540c\u65f6\u63d0\u4f9b\u65f6\u4ee5 summary_claims \u4e3a\u51c6\u3002ID \u5fc5\u987b\u662f\u672c\u53c2\u6570\u4e2d claims\uff08sections/recommendations \u5185\uff09\u771f\u5b9e\u5b58\u5728\u7684 id\u3002",
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
