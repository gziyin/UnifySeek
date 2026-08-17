from __future__ import annotations

from pathlib import Path

from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.evidence import EvidenceRecord
from ai_dev_researcher.domain.reports import (
    ReportSection,
    ReportTable,
    ResearchClaim,
    ResearchReport,
)


def _build_numbering(
    report: ResearchReport, claims_by_id: dict[str, ResearchClaim]
) -> dict[str, int]:
    """按渲染顺序（核心结论 → sections 深度优先含 table → 资料冲突 → 行动建议）
    首次出现顺序遍历全部 citation_ids，建立 evidence_id → 1-based 编号映射（去重）。"""
    order: list[str] = []
    seen: set[str] = set()

    def add(cids: list[str]) -> None:
        for cid in cids:
            if cid not in seen:
                seen.add(cid)
                order.append(cid)

    if report.summary_claims:
        for claim in report.summary_claims:
            add(claim.citation_ids)
    else:
        for claim_id in report.executive_summary_claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is not None:
                add(claim.citation_ids)

    def walk_section(sec: ReportSection) -> None:
        for claim in sec.claims:
            add(claim.citation_ids)
        if sec.table is not None:
            add(sec.table.citation_ids)
        for sub in sec.subsections:
            walk_section(sub)

    for sec in report.sections:
        walk_section(sec)

    for item in report.disagreements:
        for side in item.sides:
            add(side.citation_ids)

    for claim in report.recommendations:
        add(claim.citation_ids)

    return {cid: i + 1 for i, cid in enumerate(order)}


_REFINE_SUMMARY_MAX_CHARS = 200

_SENTENCE_END = "。！？!?"
_CLAUSE_END = "；;、，,:："


def _truncate_summary(text: str, limit: int = _REFINE_SUMMARY_MAX_CHARS) -> str:
    """核心结论精炼：先剥掉 markdown 强调/代码标记用于度量；
    未超限则原样返回（保留粗体）；超限则按「句末标点 → 分句/词边界 → 硬切」截断，
    不追加省略号，避免半词/半句与未闭合 ** 标记（#43）。"""
    cleaned = text.replace("**", "").replace("`", "").replace("*", "")
    if len(cleaned) <= limit:
        return text
    window = cleaned[:limit]
    cut = -1
    for class_ in (_SENTENCE_END, _CLAUSE_END):
        idx = max(window.rfind(ch) for ch in class_)
        if idx != -1:
            cut = idx + 1
            break
    if cut == -1:
        idx = window.rfind(" ")
        cut = idx + 1 if idx != -1 else limit
    out = window[:cut].rstrip()
    return out if out else cleaned[:limit].rstrip()


def _render_sources_line(
    citation_ids: list[str], numbering: dict[str, int]
) -> str | None:
    """章节聚合来源行（章节内首次出现顺序、去重），灰斜体 *来源：[n][n]...*。"""
    seen: list[str] = []
    for cid in citation_ids:
        if cid in numbering and cid not in seen:
            seen.append(cid)
    if not seen:
        return None
    cites = "".join(f"[{numbering[c]}]" for c in seen)
    return f"*来源：{cites}*"


def _render_claim_paragraph(claim: ResearchClaim) -> str:
    """段落式 claim：statement（可含粗体）原样输出，引用由章节末尾聚合标注。"""
    return claim.statement


def _render_table(table: ReportTable) -> list[str]:
    out: list[str] = [
        "| " + " | ".join(table.columns) + " |",
        "|" + "|".join(["---"] * len(table.columns)) + "|",
    ]
    for row in table.rows:
        out.append("| " + " | ".join(row) + " |")
    return out


def _collect_section_cites(sec: ReportSection) -> list[str]:
    """收集整棵子树（claims + table + subsections）的 citation_ids，章节内首次出现去重。"""
    order: list[str] = []
    seen: set[str] = set()

    def add(cids: list[str]) -> None:
        for cid in cids:
            if cid not in seen:
                seen.add(cid)
                order.append(cid)

    def walk(s: ReportSection) -> None:
        for claim in s.claims:
            add(claim.citation_ids)
        if s.table is not None:
            add(s.table.citation_ids)
        for sub in s.subsections:
            walk(sub)

    walk(sec)
    return order


def _render_section(
    lines: list[str], sec: ReportSection, numbering: dict[str, int], level: int
) -> None:
    lines.append(f"{'#' * level} {sec.heading}")
    lines.append("")
    for claim in sec.claims:
        lines.append(_render_claim_paragraph(claim))
    lines.append("")
    if sec.table is not None:
        lines.extend(_render_table(sec.table))
        lines.append("")
    for sub in sec.subsections:
        _render_section(lines, sub, numbering, level + 1)
    source_line = _render_sources_line(_collect_section_cites(sec), numbering)
    if source_line:
        lines.append(source_line)
        lines.append("")


def render_report_markdown(
    report: ResearchReport,
    claims_by_id: dict[str, ResearchClaim],
    evidence_by_id: dict[str, EvidenceRecord] | None = None,
) -> str:
    numbering = _build_numbering(report, claims_by_id)
    lines: list[str] = [f"# {report.title}", ""]

    lines.append("## 核心结论")
    lines.append("")
    if report.summary_claims:
        for claim in report.summary_claims:
            lines.append(_truncate_summary(claim.statement))
        summary_citation_ids = [
            cid for claim in report.summary_claims for cid in claim.citation_ids
        ]
    else:
        for claim_id in report.executive_summary_claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is not None:
                lines.append(_truncate_summary(claim.statement))
        summary_citation_ids = [
            cid
            for claim_id in report.executive_summary_claim_ids
            for cid in (
                claims_by_id.get(claim_id).citation_ids
                if claims_by_id.get(claim_id) is not None
                else []
            )
        ]
    source_line = _render_sources_line(summary_citation_ids, numbering)
    if source_line:
        lines.append("")
        lines.append(source_line)
    lines.append("")

    for sec in report.sections:
        _render_section(lines, sec, numbering, level=2)

    if report.disagreements:
        lines.append("## 资料冲突")
        lines.append("")
        for item in report.disagreements:
            lines.append(f"### {item.topic}")
            lines.append("")
            for side in item.sides:
                lines.append(f"- {side.position}")
            lines.append("")
        source_line = _render_sources_line(
            [
                cid
                for item in report.disagreements
                for side in item.sides
                for cid in side.citation_ids
            ],
            numbering,
        )
        if source_line:
            lines.append(source_line)
            lines.append("")

    if report.unknowns:
        lines.append("## 未知项")
        lines.append("")
        for unknown in report.unknowns:
            lines.append(f"- {unknown}")
        lines.append("")

    if report.recommendations:
        lines.append("## 行动建议")
        lines.append("")
        for claim in report.recommendations:
            lines.append(_render_claim_paragraph(claim))
        source_line = _render_sources_line(
            [cid for claim in report.recommendations for cid in claim.citation_ids],
            numbering,
        )
        if source_line:
            lines.append("")
            lines.append(source_line)
        lines.append("")

    if evidence_by_id and numbering:
        lines.append("### Sources")
        lines.append("")
        for cid, n in numbering.items():
            evidence = evidence_by_id.get(cid)
            url = (
                (evidence.canonical_url or evidence.locator)
                if evidence is not None
                else ""
            )
            if url:
                lines.append(f"- [{n}] {url}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def atomic_write_text(target: Path, content: str, *, root: Path) -> Path:
    ensured = ensure_within_root(target, root)
    ensured.parent.mkdir(parents=True, exist_ok=True)
    tmp = ensured.with_suffix(ensured.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(ensured)
    return ensured


def collect_claims(report: ResearchReport) -> dict[str, ResearchClaim]:
    claims: dict[str, ResearchClaim] = {}

    def add_claim(claim: ResearchClaim) -> None:
        claims[claim.id] = claim

    def walk_section(sec: ReportSection) -> None:
        for claim in sec.claims:
            add_claim(claim)
        for sub in sec.subsections:
            walk_section(sub)

    for section in report.sections:
        walk_section(section)
    for claim in report.summary_claims:
        add_claim(claim)
    for claim in report.recommendations:
        add_claim(claim)
    return claims
