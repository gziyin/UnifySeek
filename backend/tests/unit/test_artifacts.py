from __future__ import annotations

from uuid import uuid4

from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.reports import (
    Disagreement,
    DisagreementSide,
    ReportSection,
    ReportTable,
    ResearchClaim,
    ResearchReport,
)
from ai_dev_researcher.storage.artifacts import collect_claims, render_report_markdown


def _claim(cid: str, cids: list[str], confidence: str = "medium") -> ResearchClaim:
    return ResearchClaim(
        id=cid, statement=f"statement {cid}", citation_ids=cids, confidence=confidence
    )


def _evidence(
    eid: str, *, url: str | None = None, locator: str | None = None
) -> EvidenceRecord:
    return EvidenceRecord(
        id=eid,
        run_id=uuid4(),
        source_type="web",
        evidence_level="first_party",
        title=f"title {eid}",
        locator=locator or url or f"locator-{eid}",
        canonical_url=url,
        excerpt=f"excerpt {eid}",
    )


def _nested_report() -> ResearchReport:
    """执行摘要 C1 / 分节 C2 + table / 子节 C3 / 建议 CR。"""
    return ResearchReport(
        title="DeepAgents 对比调研",
        executive_summary_claim_ids=["C1"],
        sections=[
            ReportSection(
                heading="一、架构对比",
                claims=[_claim("C1", ["S1"]), _claim("C2", ["S2", "S3"])],
                table=ReportTable(
                    columns=["维度", "DeepAgents", "LangGraph"],
                    rows=[["编排", "主从", "图"]],
                    citation_ids=["S3"],
                ),
                subsections=[
                    ReportSection(
                        heading="1.1 编排差异", claims=[_claim("C3", ["S4"])]
                    )
                ],
            )
        ],
        recommendations=[_claim("CR", ["S1"])],
    )


def _evidence_map() -> dict[str, EvidenceRecord]:
    return {
        "S1": _evidence("S1", url="https://example.com/1"),
        "S2": _evidence("S2", locator="lines 1-3"),  # 无 canonical_url，回退 locator
        "S3": _evidence("S3", url="https://example.com/3"),
        "S4": _evidence("S4", url="https://example.com/4"),
    }


def _document_evidence(eid: str, artifact_id: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        id=eid,
        run_id=uuid4(),
        source_type="document",
        evidence_level="user_document",
        title="采购合同金额条款",
        locator="lines 20-25",
        excerpt="合同金额为……",
        artifact_id=artifact_id,
        page=3,
        line_start=20,
        line_end=25,
    )


def test_render_contains_all_sections():
    md = render_report_markdown(
        _nested_report(), collect_claims(_nested_report()), _evidence_map()
    )
    for fragment in [
        "# DeepAgents 对比调研",
        "## 核心结论",
        "## 一、架构对比",
        "### 1.1 编排差异",
        "## 行动建议",
        "### Sources",
    ]:
        assert fragment in md, f"missing: {fragment!r}"


def test_render_numbered_citations_and_sources():
    report = _nested_report()
    md = render_report_markdown(report, collect_claims(report), _evidence_map())
    # 编号顺序：C1[S1]=1, C2[S2,S3]=2/3, table[S3](dup), C3[S4]=4, CR[S1](dup)
    # 句尾不再内联 [n]，改为章节末尾聚合来源行
    assert "statement C1" in md
    assert "statement C2" in md
    assert "statement C3" in md
    assert "statement CR" in md
    assert "statement C1[1]" not in md
    assert "statement C2[2][3]" not in md
    # 核心结论聚合：C1[S1]
    assert "*来源：[1]*" in md
    # 章节聚合（含 table 与子节，章节内首次出现顺序）：S1,S2,S3,S4
    assert "*来源：[1][2][3][4]*" in md
    # 行动建议聚合：CR[S1]
    assert md.count("*来源：[1]*") >= 1
    # Sources 按编号列出，doc 证据回退 locator
    assert "- [1] https://example.com/1" in md
    assert "- [2] lines 1-3" in md
    assert "- [3] https://example.com/3" in md
    assert "- [4] https://example.com/4" in md


def test_render_omits_confidence_and_evidence_ids():
    md = render_report_markdown(_nested_report(), collect_claims(_nested_report()))
    assert "confidence=" not in md
    assert "confidence" not in md.lower()
    # 证据 ID（S1/D1 形态）不应出现在正文
    for cid in ["S1", "S2", "S3", "S4"]:
        assert cid not in md


def test_render_multilevel_headings():
    md = render_report_markdown(_nested_report(), collect_claims(_nested_report()))
    assert "## 一、架构对比" in md
    assert "### 1.1 编排差异" in md


def test_render_table_as_markdown_rows():
    md = render_report_markdown(_nested_report(), collect_claims(_nested_report()))
    assert "| 维度 | DeepAgents | LangGraph |" in md
    assert "| 编排 | 主从 | 图 |" in md
    # 表格级引用不再内联，并入章节末尾聚合来源行
    assert "statement C1" in md
    assert "*来源：[1][2][3][4]*" in md


def test_render_aggregated_sources_order():
    """聚合行按章节内首次出现顺序（非全局编号升序）：C2[S2,S3] 先于子节 C3[S4]。"""
    report = _nested_report()
    md = render_report_markdown(report, collect_claims(report), _evidence_map())
    # 章节一、架构对比 引用顺序：C1[S1] → C2[S2,S3] → table[S3](dup) → 子节 C3[S4]
    assert "*来源：[1][2][3][4]*" in md


def test_render_summary_within_cap_renders_full_with_bold():
    """核心结论未超上限（≤200）时原样渲染（含粗体），不追加省略号（#43，批次 F）。"""
    report = ResearchReport(
        title="t",
        executive_summary_claim_ids=["LONG"],
        sections=[
            ReportSection(
                heading="H",
                claims=[_claim("LONG", ["S1"], "medium")],
            )
        ],
        recommendations=[_claim("CR", ["S1"], "medium")],
    )
    long_statement = "这是一段**非常长的核心结论**" + "内容" * 60  # 清洗后约 133 < 200
    report.sections[0].claims[0].statement = long_statement
    md = render_report_markdown(report, collect_claims(report), _evidence_map())
    assert "## 核心结论" in md
    assert "…" not in md
    assert long_statement in md  # 正文 section 仍完整渲染
    core_block = md.split("## 核心结论", 1)[1].split("## ", 1)[0]
    assert long_statement in core_block  # 核心结论原样保留（含粗体）
    assert "**非常长的核心结论**" in core_block


def test_render_summary_over_cap_cuts_sentence_boundary_no_ellipsis():
    """核心结论超上限（>200）时按句边界截断、不补省略号、截断段去强调（#43，批次 F）。"""
    report = ResearchReport(
        title="t",
        executive_summary_claim_ids=["LONG"],
        sections=[
            ReportSection(
                heading="H",
                claims=[_claim("LONG", ["S1"], "medium")],
            )
        ],
        recommendations=[_claim("CR", ["S1"], "medium")],
    )
    long_statement = "**超长**：第一句完整结论。" + ("后续内容非常长。" * 60)  # 远超 200
    report.sections[0].claims[0].statement = long_statement
    md = render_report_markdown(report, collect_claims(report), _evidence_map())
    assert "…" not in md
    assert long_statement in md  # 正文 section 仍是完整原文（含粗体）
    core_block = md.split("## 核心结论", 1)[1].split("## ", 1)[0]
    para = core_block.strip().split("\n\n*来源")[0].strip()
    assert para.endswith("。")
    assert "**" not in core_block  # 核心结论截断段去强调，无未闭合标记


def test_collect_claims_recurses_into_subsections():
    claims = collect_claims(_nested_report())
    assert set(claims.keys()) == {"C1", "C2", "C3", "CR"}
    assert claims["C3"].statement == "statement C3"


def test_render_strips_inline_citation_tokens_from_free_text():
    summary = ResearchClaim(
        id="summary",
        statement="Core conclusion [S1] remains prose",
        citation_ids=["S1"],
        confidence="medium",
    )
    section_claim = ResearchClaim(
        id="section",
        statement="Body [S2][D1] continues",
        citation_ids=["S2"],
        confidence="medium",
    )
    recommendation = ResearchClaim(
        id="recommendation",
        statement="Recommendation [K2] closes",
        citation_ids=["S1"],
        confidence="medium",
    )
    report = ResearchReport(
        title="t",
        summary_claims=[summary],
        sections=[
            ReportSection(
                heading="Heading",
                claims=[section_claim],
                table=ReportTable(
                    columns=["Dimension"],
                    rows=[["Cell [4] remains"]],
                    citation_ids=["S2"],
                ),
            )
        ],
        disagreements=[
            Disagreement(
                topic="Topic",
                claim_ids=["section"],
                sides=[
                    DisagreementSide(position="Position [s3] remains", citation_ids=["S1"]),
                    DisagreementSide(position="Other position", citation_ids=["S2"]),
                ],
            )
        ],
        unknowns=["Unknown item [1] remains"],
        recommendations=[recommendation],
    )
    md = render_report_markdown(report, collect_claims(report))
    free_text = "\n".join(
        line
        for line in md.splitlines()
        if not line.startswith("*\u6765\u6e90\uff1a") and not line.startswith("- [")
    )

    for token in ("[S1]", "[S2]", "[D1]", "[K2]", "[s3]", "[1]", "[4]"):
        assert token not in free_text
    for fragment in (
        "Core conclusion remains prose",
        "Body continues",
        "Recommendation closes",
        "Position remains",
        "Unknown item remains",
        "Cell remains",
    ):
        assert fragment in md
    assert "  " not in free_text


def test_render_generated_sources_survive_inline_strip():
    report = _nested_report()
    md = render_report_markdown(report, collect_claims(report), _evidence_map())

    assert "*\u6765\u6e90\uff1a[1][2][3][4]*" in md
    assert "### Sources" in md
    assert "- [1] https://example.com/1" in md


def test_render_document_source_uses_filename_title_and_location():
    artifact_id = uuid4()
    evidence = _document_evidence("D1", str(artifact_id))
    report = ResearchReport(
        title="document report",
        summary_claims=[_claim("C1", ["D1"])],
        sections=[],
        recommendations=[_claim("R1", ["D1"])],
    )
    md = render_report_markdown(
        report,
        collect_claims(report),
        {"D1": evidence},
        {str(artifact_id): "采购合同.pdf"},
    )

    assert "- [1] 采购合同.pdf — 采购合同金额条款（第 3 页，第 20–25 行）" in md


def test_render_document_source_falls_back_when_artifact_is_missing():
    evidence = _document_evidence("D1", str(uuid4()))
    report = ResearchReport(
        title="document report",
        summary_claims=[_claim("C1", ["D1"])],
        sections=[],
        recommendations=[_claim("R1", ["D1"])],
    )
    md = render_report_markdown(report, collect_claims(report), {"D1": evidence}, {})

    assert "- [1] 采购合同金额条款 — lines 20-25" in md


def test_render_web_source_format_is_unchanged_with_document_sources():
    artifact_id = uuid4()
    document = _document_evidence("D1", str(artifact_id))
    web = _evidence("S1", url="https://example.com/source")
    report = ResearchReport(
        title="mixed report",
        summary_claims=[_claim("C1", ["S1", "D1"])],
        sections=[],
        recommendations=[_claim("R1", ["S1"])],
    )
    md = render_report_markdown(
        report,
        collect_claims(report),
        {"S1": web, "D1": document},
        {str(artifact_id): "采购合同.pdf"},
    )

    assert "- [1] https://example.com/source" in md
    assert "- [2] 采购合同.pdf — 采购合同金额条款（第 3 页，第 20–25 行）" in md


def test_render_each_document_evidence_and_omit_repeated_filename_title():
    artifact_id = uuid4()
    first = _document_evidence("D1", str(artifact_id)).model_copy(
        update={"title": "采购合同.pdf"}
    )
    second = first.model_copy(
        update={
            "id": "D2",
            "page": 4,
            "line_start": 30,
            "line_end": 35,
            "locator": "lines 30-35",
        }
    )
    report = ResearchReport(
        title="duplicate report",
        summary_claims=[_claim("C1", ["D1", "D2"])],
        sections=[],
        recommendations=[_claim("R1", ["D1"])],
    )
    md = render_report_markdown(
        report,
        collect_claims(report),
        {"D1": first, "D2": second},
        {str(artifact_id): "采购合同.pdf"},
    )

    assert "- [1] 采购合同.pdf（第 3 页，第 20–25 行）" in md
    assert "- [2] 采购合同.pdf（第 4 页，第 30–35 行）" in md
    assert md.count("采购合同.pdf") == 2
